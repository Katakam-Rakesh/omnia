Provision
==========

⦾ **Why does the provisioning status of RHEL remote servers remain stuck at ‘installing’ in** ``cluster.nodeinfo`` **table (omniadb)?**

.. image:: ../../../images/InstallingStuckDB.png

.. image:: ../../../images/InstallCorruptISO.png

.. csv-table::
   :file: ../../../Tables/FAQ_provision.csv
   :header-rows: 1
   :keepspace:

⦾ **Why do subscription errors occur on RHEL OIM when** ``rhel_repo_local_path`` **in** ``input/provision_config.yml`` **is not provided and OIM does not have an active subscription?**

.. image:: ../../../images/SubscriptionErrors.png

For many of Omnia's features to work, RHEL OIMs need access to the following repositories:

    1. AppStream
    2. BaseOS

This can only be achieved using local repositories specified in ``rhel_repo_local_path``.

.. note::
    To enable the repositories, run the following commands: ::

            subscription-manager repos --enable=codeready-builder-for-rhel-9-x86_64-rpms
            subscription-manager repos --enable=rhel-9-for-x86_64-appstream-rpms
            subscription-manager repos --enable=rhel-9-for-x86_64-baseos-rpms

    Verify your changes by running: ::

            yum repolist enabled