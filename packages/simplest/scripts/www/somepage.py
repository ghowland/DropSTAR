def ProcessBlock(pipe_data_input, page, request_state_input, args, tag=None, cwd=None, env=None, block_parent=None):
    # HTML Body output
    output = ''
    
    # Some static page 
    output += 'Testing 123 - Static text.'
    
    # Add a button to do a Javascript RPC call to rpc/somerpc.py
    output += '''<br><button onclick="RPCUrl('rpc/example-rpc')">Engage RPC Request to Dynamically Populate Some Stuff</button>'''
    
    # Once the RPC is called, it will automatically write output into this div, due to the ID matching the JSON dict
    output += '<br><div id="rpc_write_target"></div>'

    # Return the output to the %(body)s template.  Template can have any number of these, the basic 
    #   template just has body.
    data = {'body':output, 'title':'Some page example'}
    return data
