#!/usr/bin/python


#Author: Geoff Howland
#Project: Drop Star                  http://redeyemon.sourceforge.net/dropstar/
#Licensed under the MIT License:     http://en.wikipedia.org/wiki/MIT_License

"""
httpd

Handles all HTTP requests, GET only.  It is intended to be READ only.

Use RPC for doing interaction with the backend, doing this inside a web
rendering request is a total pain in the ass and has all kinds of times
you'd like to have some feature you dont, and this isnt an end-user
oriented service, so is it assumed their JS will be working and can
submit any data we need them to.  Code accordingly for best results.

When querying Cloud data from the Site Control API, we will always get the
cached version, so that web requests are fast.  This is not the place we need
to wait on data.

TODO(g): Clean this file up, break into other modules to separate and harden
    the individual tasks.  Things are working and stable now, so time to harden.
"""

import sys
import time
import mimetools
import re
import BaseHTTPServer
import SocketServer
import SimpleXMLRPCServer
import urlparse
import cgi
import string
import Cookie
import socket
import sys
import os
import urllib
import traceback
import threading
import select
import jsonlib
import logging


from unidist.log import log
from unidist import error_info
from unidist import sharedlock

from procblock import processing
from procblock import procyaml

import process


# Default mount path, is root
MOUNT_HTTP_PATH_DEFAULT = '/'


def parse_multipart(fp, pdict):
    """Parse multipart input.
    
    Arguments:
    fp   : input file
    pdict: dictionary containing other parameters of content-type header
    
    Returns a dictionary just like parse_qs(): keys are the field names, each
    value is a list of values for that field.  This is easy to use but not
    much good if you are expecting megabytes to be uploaded -- in that case,
    use the FieldStorage class instead which is much more flexible.  Note
    that content-type is the raw, unparsed contents of the content-type
    header.
    
    XXX This does not parse nested multipart parts -- use FieldStorage for
    that.
    
    XXX This should really be subsumed by FieldStorage altogether -- no
    point in having two implementations of the same parsing algorithm.
    
    """
    boundary = ""
    if 'boundary' in pdict:
        boundary = pdict['boundary']
    if not cgi.valid_boundary(boundary):
        raise ValueError,  ('Invalid boundary in multipart form: %r'
                            % (boundary,))
    
    nextpart = "--" + boundary
    lastpart = "--" + boundary + "--"
    partdict = {}
    terminator = ""
    
    while terminator != lastpart:
        bytes = -1
        data = None
        if terminator:
            # At start of next part.  Read headers first.
            headers = mimetools.Message(fp)
            
            #NOTE(ghowland): This was the reason to copy this function,
            #   we want this file name!
            filename_result = re.findall('filename="(.*?)"', str(headers))
            if filename_result:
              filename_result = filename_result[0]
              if len(filename_result) > 2 and filename_result[1] == ':':
                filename_result = filename_result[2:]
              filename_result = filename_result.replace('\\', '/')
              filename_result = os.path.basename(filename_result)
              # Pack into list again, since the upload side expects that
              partdict['_filename'] = [filename_result]
            
            clength = headers.getheader('content-length')
            if clength:
                try:
                    bytes = int(clength)
                except ValueError:
                    pass
            if bytes > 0:
                if maxlen and bytes > maxlen:
                    raise ValueError, 'Maximum content length exceeded'
                data = fp.read(bytes)
            else:
                data = ""
        # Read lines until end of part.
        lines = []
        while 1:
            line = fp.readline()
            if not line:
                terminator = lastpart # End outer loop
                break
            if line[:2] == "--":
                terminator = line.strip()
                if terminator in (nextpart, lastpart):
                    break
            lines.append(line)
        # Done with part.
        if data is None:
            continue
        if bytes < 0:
            if lines:
                # Strip final line terminator
                line = lines[-1]
                if line[-2:] == "\r\n":
                    line = line[:-2]
                elif line[-1:] == "\n":
                    line = line[:-1]
                lines[-1] = line
                data = "".join(lines)
        line = headers['content-disposition']
        if not line:
            continue
        key, params = cgi.parse_header(line)
        if key != 'form-data':
            continue
        if 'name' in params:
            name = params['name']
        else:
            continue
        if name in partdict:
            partdict[name].append(data)
        else:
            partdict[name] = [data]
    
    return partdict



def CGIArgsToDict(args):
  """Convert our args string into a dictionary."""
  # Get our arg data
  data = {}
  
  for item in args.split('&'):
    #NOTE(g): I am specifically including empty vars, because they
    #    are sometimes exactly what we want.
    if '=' in item:
      (key, value) = item.split('=', 1)
      key = urllib.unquote(key.replace('+', ' '))
      
      # If this key is not a list
      if not key.endswith('[]'):
        data[key] = urllib.unquote(value.replace('+', ' '))
      
      # Else, this key is a list
      else:
        # Get the real key name
        key_name = key[:-2]
        if key_name not in data:
          data[key_name] = []
        
        # Add the data to the array
        value = urllib.unquote(value.replace('+', ' '))
        data[key_name].append(value)
  
  return data


def UriParse(uri):
  """We want to parse the URI into a path and argument section.  Return tuple.
  
  Python's urlparse module fails on more complex data, such as sending Python
  code across the line.  It crops our data, breaking the submit, so we must do
  this ourselves.
  
  Args:
    uri: string, uri (url, minus the protocol and host name)
  
  Returns: tuple (path, args).  Both strings.
  """
  if '?' in uri:
    (path, args) = uri.split('?', 1)
  else:
    (path, args) = (uri, '')
  
  return (path, args)



class HttpdThread(threading.Thread):
  """HTTP Listener Thread"""

  def __init__(self, port, port_sites, conf):
    self.port = port
    self.port_sites = port_sites
    self.conf = conf
    
    # Server objects: To be populated later
    self.server = None
    self.fd_server = None
    
    log('Starting HTTP Listener: %s' % port, logging.INFO)
    
    # Initialize this as a Thread
    threading.Thread.__init__(self)

  
  def run(self):
    """Once start() is called, this function is executed, which is the thread's
    run function.
    """
    #TODO(g): Allow specifying the interface, for localhost only or flexibility
    self.server = BaseHTTPServer.HTTPServer(('0.0.0.0', self.port), HTTPRequest)
    self.fd_server = self.server.fileno()
    
    # Save the sites for this port, so the HTTPRequest handler can process
    self.server.sites = self.port_sites
    self.server.conf = self.conf
    
    # Loop forever, or until we quit, whichever comes first
    while sharedlock.IsLocked('__running'):
      try:
        #log('HTTP Listener (%s): Waiting for request...' % self.port, logging.DEBUG)
        (wait_in, wait_out, wait_err) = select.select([self.fd_server], [self.fd_server], [], 0)
        
        # Handle HTTP request
        if self.fd_server in wait_in or self.fd_server in wait_out:
          self.server.handle_request()
        
        # Give back to the system as we spin loop
        time.sleep(0.001)
      
      # Log and ignore
      except Exception, e:
        exception_output = traceback.format_exc()
        log('HttpdThread: Unhandled exception:\n%s' % exception_output, logging.ERROR)
    
    log('HTTP Listener (%s): Finished' % self.port, logging.INFO)


class HTTPRequest(BaseHTTPServer.BaseHTTPRequestHandler):
  """HTTP Request handler."""
  
  def do_GET(self):
    """Process HTTP GET request"""
    path = self.path
    
    #NOTE(g): urlparse is not good enough.  It can kill data payload in our
    #   POST requests passed to GET.  I would guess the same problem could
    #   occur with a regular GET too.  Not sure why it does this, but when
    #   trying to pass Python Code from a textarea tag I lose data as urlparse
    #   crops it right here.
    #(_, _, path, _, args, _) = urlparse.urlparse(path)
    (path, args) = UriParse(path)
    
   
    # Strip the leading slash (/)
    path = path[1:]
    
    # Having 2 var names is confusing.  We never care about the string version
    #   again.  Ditch it.
    args = CGIArgsToDict(args)
    
    try:
      # This wraps the real work of the request.  This keeps GET and other
      #   HTTP methods wrapped.
      self.handle_everything(path, args)
    except:
      text = error_info.GetExceptionDetails()
      log(text, logging.ERROR)


  def do_POST(self):
    """Process HTTP POST request"""
    path = self.path
    
    (_, _, path, _, args, _) = urlparse.urlparse(path)
    
    # Strip the leading slash (/)
    path = path[1:]
    
    # Get our args
    (ctype, pdict) = cgi.parse_header(self.headers.getheader('content-type'))
    # Normal CGI arg passing, with POST
    if ctype == 'application/x-www-form-urlencoded':
      clen = self.headers.getheader('content-length')
      if clen:
          clen = string.atoi(clen)
      data = self.rfile.read(clen)
      self.path = '%s?%s' % (self.path, data)
      
      #print 'POST: GET Path: %s' % self.path
      
      # Now we have set all the args back to someething GET understands
      self.do_GET()
      return
    
    # POST Upload
    elif ctype == 'multipart/form-data':
      query = parse_multipart(self.rfile, pdict)
      
      # Build up the args
      args = {}
      for key in query:
        args[key] = query[key][0]
      
      # Business as usual
      try:
        self.handle_everything(path, args)
      except Exception, e:
        text = GetExceptionDetails()
        log.critical(text)
        print text
      
      return
    else:
      #TODO(ghowland): Add error handling here
      log('Uncaught POST error', logging.CRITICAL)
      pass
  
  
  def handle_everything(self, path, args):
    # Get the cookies
    write_cookies = {}
    write_headers = {}
    cookies = {}
    headers = dict(self.headers)
    if self.headers.has_key("Cookie"):
      cookie = Cookie.SimpleCookie(self.headers["Cookie"])
      for name in cookie:
        cookies[name] = cookie[name].value
    
    # Get the host header, for our application
    if self.headers.has_key('X-Forwarded-Host'):
      host_header = self.headers['X-Forwarded-Host']
    elif self.headers.has_key('Host'):
      host_header = self.headers['Host']
      if ':' in host_header:
        host_header = host_header.split(':')[0]
    else:
      host_header = None
    
    # Init response data
    output = ''
    content_type = 'text/html'
    response_code = 400
    
    #log('Rendering request: %s %s %s %s' % (path, self.headers, cookies, args), logging.DEBUG)
    
    # Start the duration clock
    start_time = time.time()
    
    # Render the request 
    try:
      (output, content_type, response_code, redirect_url, write_cookies,
          write_headers) = self.RenderRequest(path, headers, cookies, args)
    
    # On render failures, report the error as best we can
    except Exception, e:
      details = error_info.GetExceptionDetails()
      log('%s' % details, logging.ERROR)
      output = details.replace('\n', '<br>\n')
      content_type = 'text/html'
      response_code = 500
    
    # Write headers
    self.send_response(response_code)
    self.send_header('Content-type', content_type)
    
    # Write cookies
    for name in write_cookies:
      self.send_header('Set-Cookie', '%s="%s"; Path=/' % (name,
                                                          write_cookies[name]))
    
    # Write headers
    for name in write_headers:
      self.send_header(name, write_headers[name])
    
    # End the headers
    self.end_headers()
    
    # Write output
    self.wfile.write(output)
    
    # Stop the duration clock
    duration = time.time() - start_time
    
    # Dont log static content, boring
    #TODO(g): Need to re-define what is static content.  This has changed.
    #if not path.startswith('///static////'):
    #if content_type != 'text/html':
    if 0:
      log('Path: %s  Duration: %0.2fs' % (path, duration), logging.INFO)


  def RenderRequest(self, path, headers, cookies, args):
    """Render this Request - Past GET/POST processing, down to business.
    
    Args:
        path: string, uri
        headers: dict (string/string), HTTP headers and their values
        bookies: dict (string/string), HTTP cookies
        args: dict (key is string, value is string), CGI key/values
    
    Returns: tuple: (output, content_type, response_code, redirect_url, write_cookies, write_headers)
        - output: string (HTML or other content)
        - content_type: HTTP/MIME content types
        - response_code: integer, HTTP status code
        - redirect_url: None or string, if string URL to redirect to (301)
        - write_coookies: dict (string/string), new cookies to present to HTTP client
        - write_headers: dict (string/string), output HTTP headers
    """
    # Initialize result data
    output = ''
    content_type = 'text/html'
    response_code = 200
    redirect_url = None
    write_cookies = {}
    write_headers = {}
    
    # Get the host and port (if specified)
    host = None
    port = None
    if 'host' in headers:
      if ':' in headers['host']:
        (host, port) = headers['host'].split(':', 1)
        port = int(port)
      else:
        host = headers['host']
    
    #log('Headers: %s' % headers)
    
    
    # Get the site from the host/port
    site_conf = process.GetSiteConf(self.server.sites, host)
    if site_conf == None:
      raise Exception('No site found for host: %s: %s' % (host, self.server.conf.keys()))
    
    
    #log('Site Conf: %s' % site_conf)
    
    # If we dont have the packages, bail out
    if 'packages' not in site_conf:
      raise Exception('"packages" not in site conf: %s' % site_conf)
    
    
    # Loop through the package and find the one that matches
    for package in site_conf['packages']:
      #log('Testing package: %s' % package)
      
      # Imports every time, has internal cache, so this method is preferred so
      #   updates can be accessed
      package_block = procyaml.ImportYaml(package['path'])
      #log('Package Pages: %s' % package_block['http'].keys())
      
      # Get the mounted paths for HTTP
      if 'mount' in package:
        http_path = package['mount']
      else:
        http_path = MOUNT_HTTP_PATH_DEFAULT
      
      # Get the mounted paths for HTTP
      if 'mount rpc' in package:
        rpc_path = package['mount rpc']
      else:
        rpc_path = '%srpc/' % http_path
      
      # Get the static fall through path
      if 'static' in package_block and 'path' in package_block['static']:
        static_file_path = package_block['static']['path']
      else:
        static_file_path = None
      
      #log('http: %s  rpc: %s  static: %s' % (http_path, rpc_path, static_file_path))
      
      output = None
      test_path = '/%s' % path
      
      # Process HTTP pages
      if 'http' in package_block:
        for (page_name, page) in package_block['http'].items():
          #log('Page: %s: %s' % (page_name, page))
          #TODO(g): Do aliases[] too
          page_mount = '%s%s' % (http_path, page_name)
          #log('Testing page: %s == %s' % (test_path, page_mount))
          if test_path == page_mount:
            #log('Found page: %s' % page)
            
            # If this page is not processed with a run_thread
            #log('Getting results procblock: "%s": %s' % (page_name, page.keys()))
            pipe_data_input = dict(args)
            # Set render to true.  This is so pages dont get rendered unless we are specifically invoking them
            pipe_data_input['render'] = "True"
            
            #TODO(g): Only render the actual block, right?
            #request_state_input = {'headers':headers, 'cookies':cookies}
            request_state_input = {'headers':headers, 'cookies':cookies, 'path':path, 'args':args, 'host':host, 'port':port, 'protocol':'http', 'page':page_name, 'page_mount':page_mount, 'package':package}
            render_out = processing.Process(pipe_data_input, page, request_state_input, args, tag=None, cwd=None, env=None, block_parent=None)
            try:
              output = render_out['template']
            except KeyError, e:
              #TODO(g): Why is this necessary?  It only started with "show" page
              output = render_out['run']['template']
      
      else:
        log('No "http" section in site_conf: %s' % site_conf.keys(), logging.WARN)
      
      
      # Process RPC functions
      if output == None and 'rpc' in package_block:
        for (rpc_name, rpc) in package_block['rpc'].items():
          rpc_mount = '%s%s' % (rpc_path, rpc_name)
          #log('Testing page: %s == %s' % (test_path, page_mount))
          if test_path == rpc_mount:
            log('Found RPC Function: %s: Args: %s' % (rpc, args))
            
            # If this page is not processed with a run_thread
            log('Getting results procblock: "%s": %s' % (rpc_name, rpc.keys()))
            pipe_data_input = dict(args)
            # Set render to true.  This is so pages dont get rendered unless we are specifically invoking them
            pipe_data_input['render'] = "True"
            #TODO(g): Only render the actual block, right?
            request_state_input = {'headers':headers, 'cookies':cookies, 'path':path, 'args':args, 'host':host, 'port':port, 'protocol':'rpc', 'rpc':rpc_name, 'rpc_mount':rpc_mount, 'package':package}
            render_out = processing.Process(pipe_data_input, rpc, request_state_input, args, tag=None, cwd=None, env=None, block_parent=None)
            #TODO(g): Clean all this junk up!  Especially the recursive 'run' dict...
            run_output = render_out['run']
            del run_output['render']
            del run_output['__start_time']
            if '__duration' in run_output:
              del run_output['__duration']
            #print 'JSON result: %s' % run_output
            
            # Write in JSON format
            json_result = jsonlib.write(run_output)
            
            # Get rid of trailing comma in result
            #TODO(g): Am I doing this wrong?  I dont know, it seems
            #   to produce a trailing comma in the HTML print...
            #   Also, seems to work even though I do this, in both text
            #   and data structures...
            output = json_result.replace('",""]', '"]')
      
      
      #log('Static File Path: %s' % static_file_path)
      
      # If we dont have the output yet, check the static files as a last result
      if output == None and static_file_path != None:
        remaining_path = path[len(http_path)-1:]
        
        static_file = '%s/%s' % (static_file_path, remaining_path)
        
        #log('Static File Search: %s' % static_file)
        
        if os.path.exists(static_file):
          # By default, open this file as a binary
          file_flag = 'rb'
          
          # Set content type by extension
          #TODO(g): Do a static content-type lookup, in YAML
          if path.lower().endswith('.png'):
            content_type = 'image/png'
          elif path.lower().endswith('.jpg'):
            content_type = 'image/jpg'
          elif path.lower().endswith('.bmp'):
            content_type = 'image/bmp'
          elif path.lower().endswith('.gif'):
            content_type = 'image/gif'
          elif path.lower().endswith('.css'):
            content_type = 'text/css'
            file_flag = 'r'
          elif path.lower().endswith('.js') or path.split('.')[-1] in ('txt', 'html'):
            content_type = 'text/html'
            file_flag = 'r'
          
          # Read the file to output, no modifications
          if os.path.isfile(static_file):
            fp = open(static_file, file_flag)
            output = fp.read()
            fp.close()
          else:
            log('Tried to read a directory: %s' % static_file)
            output = ''
        
      
      #import pprint
      #output = pprint.pformat(page).replace('\n', '<br>\n').replace(' ', '&nbsp;')
      
      return (output, content_type, response_code, redirect_url, write_cookies,
              write_headers)
