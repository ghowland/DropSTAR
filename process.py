#Author: Geoff Howland
#Project: dropSTAR                   http://redeyemon.sourceforge.net/dropstar/
#Licensed under the MIT License:     http://en.wikipedia.org/wiki/MIT_License

"""
Process page rendering.
"""

import logging

from unidist.log import log

## Improve the procblock proccessing.Process() function to process procblocks
#from processing import Process



def GetSiteConf(sites, host):
  """Get the site specified by the host in the conf."""
  #print 'GetSite: Sites: %s' % sites
  
  # Take the first site in, case none are found by host
  default = None
  
  for (site, site_conf) in sites:
    if host == site:
      return site_conf
    #TODO(g): Add tests for host aliases inside the site_conf, as we may have more than 1 way to get to a site
    elif default == None:
      default = site_conf
    
    #TODO(g): Match with regexs, and also use the 'aliases' list to match more
    #   host headers
    pass
  
  return default


def RenderPage(site, page, conf, apps, data, state):
  """Render the page."""
  output = RenderOutput()
  
  # Prepare run input
  #TODO(g): This is ghetto.  Maybe some of this should stay, but the state
  #   has it's own var in RunScriptBlock now, so we can keep application data
  run_input = {}
  run_input.update(state['headers'])
  run_input.update(state['cookies'])
  if state['session']:
    run_input.update(state['session'])
  run_input.update(data)
  
  # Run the page run script block (page already has the 'run' block in it)
  log('Running script: %s  Path: %s' % (page, site['script_path_prefix']))
  run_output = runblock.RunScriptBlock(page, run_input, state, site['script_path_prefix'])
  print '\n\n\n\n\n\n'
  print 'Run output: %s' % run_output
  
  # Templating
  if 'output' not in run_output:
    # Get the template
    template = GetTemplate(site, page, run_output)
    
    # Format the template
    template_output = FormatTemplate(template, run_output)
    
    # Add template to the output
    output += str(template_output)
    
    #TODO(g): Deal with cookies and other crap we want to set from the run_output
  
  # Direct output
  else:
    output = str(run_output['output'])
    
    # If we specified a content-type, set it into the headers
    if 'content-type' in run_output:
      state['headers']['content-type'] = run_output['content-type']
  
  return output


def GetPage(path, site_conf):
  """Get the page information from the site_conf dict."""
  log('Get Path: "%s"  Site: %s' % (path, site_conf), logging.DEBUG)
  
  # Get the page from the site
  for (name, page) in site['page'].items():
    if path in page['aliases']:
      log('Page: %s' % page['title'], logging.DEBUG)
      return page
  
  # No page found
  #TODO(g): Return site['page_not_found'] page
  #TODO(g): Return site['page_error'] page, on error
  return None

