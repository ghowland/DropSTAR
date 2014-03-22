#!/usr/bin/python2

"""
dropSTAR: Drop Scripts, Templates and RPC

Wraps HTTP handler to pass in state.  Simple and light.
"""

import logging
import sys
import time
import os

from unidist.log import log
from procblock import procyaml
from unidist import sharedlock

import httpd


# This is the default listening port, so it can be run by non-root user
DEFAULT_LISTEN_PORT = 8080


class DropStar:
  
  def __init__(self, sites):
    """Returns a dropSTAR server object, which wraps everything you need.
    
    HTTP and XMLRPC servers run in their own threads on starting.  Many sites
    may be run off one set of thread listeners on a single port, or many thread
    listeners can be specified on their respective ports.
    
    This allows many dropSTAR installations on a single machine, and the ability
    to add totally new sites to an existing dropSTAR installation without
    running a second dropSTAR process or interfering with any of the existing
    dropSTAR sites.
    
    
    Args:
      sites: dict, keyed on site main host-header for listening.  Contains
          a dictionary with site information such as the key "port", which
          specifies the port to listen on, and key "packages", which contains
          a list of packages and their mount points.
    """
    self.sites = sites
    
    log('DropStar: __init__: %s' % sites)
    
    # Load these from the sites
    self.conf = {}
    
    # Ports that sites have configured for listening
    self.ports = {}
    
    # Listening threads for ports
    self.listeners = {}
    
    # Load the dropSTAR configuration
    self.Load()
    
    # Start the dropSTAR site listening thread pools
    self._Start()
  
  
  def Load(self):
    """Load the YAML configuration file.  Sets self.conf"""
    for site_conf_filename in self.sites:
      conf = procyaml.ImportYaml(site_conf_filename)
      #log('Load: %s' % site_conf_filename)
      
      # Save this site configuration
      self.conf[site_conf_filename] = conf
      
      # Process each of the site entries in this conf file (can be many)
      for (site_name, site_conf) in conf.items():
        if 'port' in site_conf:
          port = site_conf['port']
        else:
          port = DEFAULT_LISTEN_PORT
          log('Site configuration does not specify port: %s.%s:  Using default: %s' % (site_conf_filename, site_name, port), logging.INFO)
        
        # Create our port entry
        if port not in self.ports:
          self.ports[port] = []
        
        # Add this (site_name, site_conf_filename) to this port, so we have match
        port_sites = (site_name, site_conf)
        self.ports[port].append(port_sites)
        log('Loaded port: %s: %s' % (site_name, site_conf_filename))
  
  
  def _Start(self):
    """Start all the listening pools."""
    # Start listening thread poor for each port
    for (port, port_sites) in self.ports.items():
      log('Creating Listener: %s (%s site(s))' % (port, len(port_sites)), logging.INFO)
      
      # Create the listening thread pool
      self.listeners[port] = httpd.HttpdThread(port, port_sites, self.conf)
      
      # Start it running now
      self.listeners[port].start()


def Usage(error=None):
  if error:
    code = 1
    print 'error: %s\n' % error
    
  else:
    code = 0
  
  print 'usage: %s <package YAML file 1> <package YAML file 2> <package YAML file 3> ...'
  
  sys.exit(code)


def Main(args=None):
  if not args:
    args = []
  
  # Test for no output, to give an error (redundant, but avove will stay and this will likey go away)
  if not args:
    Usage('No package files specified.')
    
  
  #TODO(g): Get options from CLI args, this is too simple long term...
  sites = list(args)
  
  for site in sites:
    if not os.path.isfile(site):
      Usage('Argument specified is not a valid site YAML file: no file found')
  
  ds = DropStar(sites)
  
  sharedlock.Acquire('__running')
  
  while sharedlock.IsLocked('__running'):
    try:
        time.sleep(0.1)
    except (KeyboardInterrupt, SystemExit): # KeyboardInterrupt -- TODO(g): Properly handle this...  
      sharedlock.Release('__running')
  

if __name__ == '__main__':
  #TODO(g): Make this into a real CLI driven interface...
  Main(sys.argv[1:])
  
