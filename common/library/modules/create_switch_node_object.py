# Copyright 2025 Dell Inc. or its subsidiaries. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

#!/usr/bin/python

import subprocess
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.discovery.omniadb_connection import execute_select_query

def create_node_object(oim_admin_ip):
    """
    Create node objects in the database based on switch information.
    """
    sql = '''SELECT switch_name, switch_port FROM cluster.nodeinfo WHERE switch_port IS NOT NULL and switch_name IS NOT NULL'''
    switch_port_output = execute_select_query(query=sql)

    if not switch_port_output:
        return "No switch information found in the database"
    msg = ''
    for switch in switch_port_output:
        switch_name = switch['switch_name']
        switch_port = switch['switch_port']
        sql = f"SELECT * FROM cluster.nodeinfo WHERE switch_port = %s AND switch_name = %s"
        query_result = execute_select_query(query=sql, params=(switch_port, switch_name))
        groups_switch_based = 'switch_based,all'
        if not query_result:
            msg += f"No matching rows found for switch_port={switch_port} and switch_name={switch_name}\n"
        else:
            row_output = query_result[0]
            groups_switch_based += ',' + row_output['role']
            groups_switch_based += ',' + row_output['group_name']
            command = [
                "/opt/xcat/bin/chdef", row_output['node'],
                f"groups={groups_switch_based}", "mgt=ipmi", "cons=ipmi",
                f"ip={row_output['admin_ip']}", f"bmc={row_output['bmc_ip']}",
                "netboot=xnba", "installnic=mac", "primarynic=mac",
                f"switch={row_output['switch_name']}", f"switchport={row_output['switch_port']}",
                f"xcatmaster={oim_admin_ip}"
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                msg += f"Error creating node {row_output['node']}: {result.stderr}\n"
            else:
                msg += f"Created node object {row_output['node']} for switch details - ip: {row_output['switch_ip']}, port: {row_output['switch_port']}, name: {row_output['switch_name']}\n"
    return msg

def main():
    """
    Main function to handle the create_switch_node_object module execution.
    It retrieves switch information from the database and creates node objects.

    Input:
    - oim_admin_ip: The IP address of the OIM admin server.

    """
    module_args = {
        'oim_admin_ip': {'type': 'str', 'required': True}
    }
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    oim_admin_ip = module.params['oim_admin_ip']
    results = create_node_object(oim_admin_ip)

    module.exit_json(changed=True, results=results)

if __name__ == '__main__':
    main()
