Best Practices
==============

* Ensure that PowerCap policy is disabled and the BIOS system profile is set to 'Performance' on the OIM.
* Always execute playbooks within the directory they reside in. That is, always change directories (``cd``) to the path where the playbook resides before running the playbook.
* Ensure that there is at least 50% (~50GB) free space on the OIM root partition before running Omnia. To maintain the free space required, place the required ISO files in the ``/home`` directory.
* Use a `PXE mapping file <OmniaInstallGuide/samplefiles.html#pxe-mapping-file-csv>`_ even when using DHCP configuration to ensure that IP assignments remain persistent across OIM reboots.
* Avoid rebooting the OIM as much as possible to ensure that all network configuration does not get disturbed.
* Review the prerequisites before running Omnia scripts.
* Ensure that the firefox version being used on the RHEL OIM is the latest available. This can be achieved using ``dnf update firefox -y``
* It is recommended to configure devices using Omnia playbooks for better interoperability and ease of access.
* Ensure that the ``/var`` partition has adequate space to execute commands and store images.
* Run ``yum update --security`` routinely on the RHEL OIM for the latest security updates.