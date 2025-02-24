Software Update
++++++++++++++++++

To install multiple packages on cluster nodes in a bulk operation, the ``software_update.yml`` playbook can be leveraged.

**Prerequisites**

    * All cluster nodes should be running RHEL OS.
    * Download the packages using ``local_repo.yml``.
    * Verify that the cluster nodes are in the ``booted`` state.


To customize the software update, enter the following parameters in ``utils/software_update/software_update_config.yml``:

.. csv-table:: Parameters for software_update_config.yml
    :file: ../Tables/software_update_config.csv
    :header-rows: 1
    :keepspace:

To run the playbook, run the following commands: ::

    cd utils/software_update
    ansible-playbook software_update.yml -i inventory

Inventory should contain the IP/hostname/service tag of the cluster nodes. For example, ::

    10.5.0.101
    10.5.0.102