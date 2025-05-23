#!/usr/bin/python

import json
import logging
import os
import re
import shutil
import tempfile
from subprocess import PIPE, Popen
from urllib.parse import urlparse

# Third-party libraries
from git import Repo
from packaging.version import InvalidVersion, Version
from prettytable import PrettyTable
import yaml

# Ansible modules
from ansible.module_utils.basic import AnsibleModule

SKIP_TYPES = ["rpm", "deb", "manifest", "pip_module", "git"]
SKIP_PACKAGES = ["ghcr.io/k8snetworkplumbingwg/multus-cni"]

# ============================================================================================
#                              Logging Configuration
# ============================================================================================

def setup_logging(log_dir):
    """Configure logging to file and console with specified log directory"""
    try:
        # Ensure log directory exists
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, 'kubespray_processor.log')

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
        logger = logging.getLogger(__name__)
        logger.info(f"Logging configured. Log file: {log_file}")
        return logger
    except Exception as e:
        print(f"Failed to configure logging: {str(e)}")
        raise

# Initialize logger as None, will be set in run_module
logger = None

# ============================================================================================
#                              Custom Exceptions
# ============================================================================================
class DirectoryOperationError(Exception):
    """Raised when directory operations fail"""

class FileOperationError(Exception):
    """Raised when file operations fail"""

class GitOperationError(Exception):
    """Raised when git operations fail"""

class CommandExecutionError(Exception):
    """Raised when command execution fails"""

class InvalidVersionError(Exception):
    """Raised when version comparison fails"""

class InvalidInputError(Exception):
    """Raised when input validation fails"""

# ============================================================================================
#                              Common functions
# ============================================================================================
def verify_file_exists(file_path: str) -> bool:
    """Verify if a file exists.
    
    Args:
        file_path: Path to the file to verify
        
    Returns:
        bool: True if file exists, False otherwise
        
    Raises:
        FileOperationError: If verification fails
    """
    try:
        exists = os.path.exists(file_path) and os.path.isfile(file_path)
        logger.debug("File %s %s", file_path, "exists" if exists else "does not exist")
        return exists
    except OSError as e:
        logger.error("OS error verifying file existence: %s", str(e))
        raise FileOperationError(f"OS error verifying {file_path}") from e
    except Exception as e:
        logger.error("Unexpected error verifying file: %s", str(e))
        raise FileOperationError(f"Unexpected error with {file_path}") from e

def delete_directory(dir_path):
    """Safely delete a directory and its contents."""
    try:
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path)
            logger.info("Directory '%s' and its contents have been deleted.", dir_path)
        else:
            logger.warning("Directory '%s' does not exist to delete.", dir_path)
    except PermissionError as e:
        logger.error("Permission denied to delete '%s': %s", dir_path, str(e))
        raise DirectoryOperationError(f"Permission denied to delete directory: {dir_path}") from e
    except OSError as e:
        logger.error("Error deleting directory '%s': %s", dir_path, str(e))
        raise DirectoryOperationError(f"Error deleting directory: {dir_path}") from e


def recreate_directory(base_path, branch_name, kube_version):
    """
    Recreate directories for kubespray branch and version.

    Parameters:
        base_path (str): Base path for directory creation.
        branch_name (str): Name of the kubespray branch.
        kube_version (str): Kubernetes version.

    Returns:
        tuple: A tuple containing the paths to the recreated directories.
    """
    try:
        directory_path = os.path.join(base_path, f'kubespray-{branch_name}')
        version_path = os.path.join(base_path, kube_version)

        # Delete existing directories if they exist
        for path in [directory_path, version_path]:
            if os.path.exists(path):
                shutil.rmtree(path)
                logger.info("Deleted existing directory: %s", path)

        # Create new directories
        os.makedirs(directory_path, exist_ok=True)
        os.makedirs(version_path, exist_ok=True)

        logger.info("Directory created at: %s", directory_path)
        logger.info("Version directory created at: %s", version_path)

        return directory_path, version_path
    except OSError as e:
        logger.error("Error recreating directories: %s", str(e))
        raise DirectoryOperationError(f"Failed to recreate directories: {str(e)}") from e


def update_yaml_variables(clone_path, updates):
    """
    Updates variables in a YAML file.

    Parameters:
        clone_path (str): Path to the base directory containing YAML files.
        updates (dict): Dictionary containing key-value pairs to be updated.

    Returns:
        tuple: (bool indicating if update succeeded, str or None with old value if applicable).
    """
    try:
        # Determine the correct YAML file path
        possible_paths = [
            os.path.join(clone_path, 'roles/kubespray_defaults/defaults/main/main.yml'),
            os.path.join(clone_path, 'roles/kubespray-defaults/defaults/main/main.yml'),
            os.path.join(clone_path, 'roles/kubespray-defaults/defaults/main.yaml')
        ]

        actual_path = next((path for path in possible_paths if verify_file_exists(path)), None)

        if not actual_path:
            logger.error("No YAML file found in %s", clone_path)
            return False, None

        logger.info("File present in path: %s", actual_path)

        with open(actual_path, 'r') as file:
            yaml_content = yaml.safe_load(file)

        updated = False
        old_value = None

        for key, value in updates.items():
            if key in yaml_content:
                old_value = yaml_content[key]
                if not str(old_value).startswith('v'):
                    logger.info("%s old value %s new value", old_value, value)
                    value = value.lstrip('v')
                yaml_content[key] = value
                logger.info("%s key updated with value %s in the YAML file %s", key, value, actual_path)
                updated = True
            else:
                logger.warning("%s key not found in the YAML file %s", key, actual_path)

        if not updated:
            logger.warning("No variables were updated.")
            return False, None

        with open(actual_path, 'w') as file:
            yaml.safe_dump(yaml_content, file)

        return True, old_value

    except yaml.YAMLError as e:
        logger.error("YAML parsing error: %s", str(e))
        raise FileOperationError(f"YAML parsing error: {str(e)}") from e
    except (OSError, IOError) as e:
        logger.error("Error updating YAML file: %s", str(e))
        raise FileOperationError(f"Error updating YAML file: {str(e)}") from e



def update_arch_variables(clone_path, update_arch):
    """
    Updates architecture-specific variables in a YAML file.

    Parameters:
        clone_path (str): Path to the directory where the YAML file is located.
        update_arch (dict): A dictionary containing the keys and values to update in the YAML file.

    Returns:
        bool: True if at least one variable was updated, False otherwise.
    """
    possible_paths = [
        os.path.join(clone_path, 'roles/kubespray_defaults/defaults/main/download.yml'),
        os.path.join(clone_path, 'roles/kubespray-defaults/defaults/main/download.yml')
    ]
    actual_path = next((path for path in possible_paths if verify_file_exists(path)), None)

    if not actual_path:
        logger.error("No YAML file found in %s", clone_path)
        return False

    logger.info("Using YAML file: %s", actual_path)

    with open(actual_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    updated = False
    with open(actual_path, 'w', encoding='utf-8') as f:
        for line in lines:
            for key, value in update_arch.items():
                if line.strip().startswith(f"{key}:"):
                    logger.info("%s updated from line: %s -> %s: %s",
                                key, line.strip(), key, value)
                    line = f"{key}: {value}\n"
                    updated = True
            f.write(line)

    if not updated:
        logger.warning("No keys were updated.")

    return updated






def verify_directory_exists(directory_path):
    """
    Verify if a directory exists.

    Parameters:
        directory_path (str): The directory path to check.

    Returns:
        bool: True if the directory exists, False otherwise.
    """
    try:
        exists = os.path.exists(directory_path) and os.path.isdir(directory_path)
        if exists:
            logger.debug("The directory %s exists", directory_path)
        else:
            logger.debug("The directory %s does not exist", directory_path)
        return exists
    except OSError as e:
        logger.error("Error verifying directory existence: %s", str(e))
        raise DirectoryOperationError(f"Error verifying directory existence: {str(e)}") from e


def execute_command(cmd):
    """
    Execute a shell command and capture its output.

    Parameters:
        cmd (str): The command to execute.

    Returns:
        tuple: (stdout, stderr) output strings.

    Raises:
        CommandExecutionError: If the command fails.
    """
    try:
        logger.info("Executing command: %s", cmd)
        process = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True)
        out, err = process.communicate()
        rc = process.returncode

        if rc == 0:
            status = (out.decode("utf-8").strip(), err.decode("utf-8").strip())
            logger.debug("Command output: %s", status)
            return status

        error_msg = "Command '%s' failed with return code %d. Error: %s" % (
            cmd, rc, err.decode("utf-8")
        )
        logger.error("%s", error_msg)
        raise CommandExecutionError(error_msg)

    except OSError as e:
        logger.error("Error executing command '%s': %s", cmd, str(e))
        raise CommandExecutionError(f"Error executing command: {str(e)}") from e


def load_file(file_path, mode):
    """
    Load content from a file and return as a list of lines.

    Parameters:
        file_path (str): Path to the file.
        mode (str): File open mode, e.g. 'r'.

    Returns:
        list|bool: List of lines or False if file not found.
    """
    try:
        if not verify_file_exists(file_path):
            return False

        with open(file_path, mode, encoding="utf-8") as f:
            return f.read().splitlines()

    except IOError as e:
        logger.error("Error reading file %s: %s", file_path, str(e))
        raise FileOperationError(f"Error reading file: {str(e)}") from e


def load_json(file_path, mode):
    """
    Load and parse JSON content from a file.

    Parameters:
        file_path (str): Path to the JSON file.
        mode (str): File open mode, e.g. 'r'.

    Returns:
        dict|bool: Parsed JSON data or False if file not found.
    """
    try:
        if not verify_file_exists(file_path):
            return False

        with open(file_path, mode, encoding="utf-8") as f:
            return json.load(f)

    except json.JSONDecodeError as e:
        logger.error("JSON parsing error in %s: %s", file_path, str(e))
        raise FileOperationError(f"JSON parsing error: {str(e)}") from e
    except IOError as e:
        logger.error("Error reading JSON file %s: %s", file_path, str(e))
        raise FileOperationError(f"Error reading JSON file: {str(e)}") from e


def write_json(file_path, json_data):
    """
    Write JSON data to a file.

    Parameters:
        file_path (str): Path to the output file.
        json_data (dict): Data to write.

    Raises:
        FileOperationError: If writing fails.
    """
    try:
        with open(file_path, 'w', encoding="utf-8") as f:
            json.dump(json_data, f, indent=2)
        logger.info("Successfully wrote JSON data to %s", file_path)

    except IOError as e:
        logger.error("Error writing JSON file %s: %s", file_path, str(e))
        raise FileOperationError(f"Error writing JSON file: {str(e)}") from e
    except TypeError as e:
        logger.error("Invalid JSON data: %s", str(e))
        raise FileOperationError(f"Invalid JSON data: {str(e)}") from e


# ============================================================================================
#                              Git related functions
# ============================================================================================



def is_version_within_range(clone_to_path, input_version, arch):
    """
    Check if the given input_version is within the kubelet_checksums range for a specific architecture.

    Parameters:
        clone_to_path (str): Path to the cloned kubespray directory.
        input_version (str): The Kubernetes version to check.
        arch (str): Architecture name (e.g., 'amd64').

    Returns:
        bool: True if input_version is within the available range, False otherwise.

    Raises:
        FileOperationError: If YAML read or parsing fails.
        InvalidVersionError: If version is present with invalid checksum or format.
    """
    try:
        possible_paths = [
            os.path.join(clone_to_path, 'roles/kubespray-defaults/defaults/main/checksums.yml'),
            os.path.join(clone_to_path, 'roles/kubespray_defaults/vars/main/checksums.yml')
        ]

        actual_path = next((p for p in possible_paths if verify_file_exists(p)), None)
        if not actual_path:
            logger.error("No YAML file found in possible paths.")
            return False

        logger.info("Using checksums file at: %s", actual_path)

        with open(actual_path, 'r', encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if 'kubelet_checksums' not in data:
            logger.warning("Missing 'kubelet_checksums' in YAML.")
            return False

        if arch not in data['kubelet_checksums']:
            logger.warning("Architecture '%s' not found in 'kubelet_checksums'", arch)
            return False

        # Extract versions
        raw_versions = data['kubelet_checksums'][arch]
        version_keys = raw_versions.keys()
        version_list = sorted([Version(v.lstrip('v')) for v in version_keys])

        if not version_list:
            logger.warning("No versions found under architecture '%s'", arch)
            return False

        version_min = version_list[0]
        version_max = version_list[-1]
        input_ver = Version(input_version.lstrip('v'))

        if input_ver not in version_list:
            logger.warning(
                "%s is not an exact match in available versions: %s in architecture '%s'",
                input_ver, [str(v) for v in version_list], arch
            )

        cleaned_versions = {k.lstrip('v'): v for k, v in raw_versions.items()}
        logger.info("Cleaned version list: %s", cleaned_versions)

        if input_ver in version_list and cleaned_versions.get(input_version.lstrip('v')) == 0:
            raise InvalidVersionError(
                f"{input_version.lstrip('v')} version found under architecture '{arch}' "
                f"is having checksum value '0'"
            )

        result = version_min <= input_ver <= version_max
        logger.info("Version %s within range %s to %s: %s",
                    input_version, version_min, version_max, result)
        return result

    except yaml.YAMLError as e:
        logger.error("YAML parsing error: %s", str(e))
        raise FileOperationError(f"YAML parsing error: {str(e)}") from e
    except InvalidVersion as e:
        logger.error("Invalid version format: %s", str(e))
        raise InvalidVersionError(f"Invalid version format: {str(e)}") from e
    except (OSError, IOError) as e:
        logger.error("Unexpected file access error: %s", str(e))
        raise FileOperationError(f"Unexpected file access error: {str(e)}") from e




def get_latest_tag(repo_url):
    """
    Get the latest valid semantic version tag from the repository.

    Parameters:
        repo_url (str): The Git repository URL.

    Returns:
        str: The latest tag name.

    Raises:
        GitOperationError: If no valid tags are found or repo clone fails.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        logger.info("Fetching latest tag from %s", repo_url)
        repo = Repo.clone_from(repo_url, temp_dir)
        repo.git.fetch('--tags')

        valid_tags = []
        for tag in repo.tags:
            try:
                version = Version(tag.name.lstrip('v'))
                valid_tags.append((version, tag))
            except InvalidVersion:
                logger.warning("Skipping invalid version tag: %s", tag.name)

        if not valid_tags:
            logger.error("No valid tags found in repository")
            raise GitOperationError("No valid tags found in repository")

        latest_tag = sorted(valid_tags, key=lambda t: t[0], reverse=True)[0][1]
        logger.info("Latest tag found: %s", latest_tag.name)
        return latest_tag.name

    except (OSError, GitOperationError) as e:
        logger.error("Error fetching tags: %s", str(e))
        raise GitOperationError(f"Error fetching tags: {str(e)}") from e
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError as e:
            logger.warning("Error cleaning up temp directory: %s", str(e))


def kubespray_dir_name(tag_name):
    """
    Generate directory name for a given kubespray tag.

    Parameters:
        tag_name (str): The Git tag name.

    Returns:
        str: Directory name formatted as 'kubespray-<tag>'.
    """
    return f"kubespray-{tag_name}"


def clone_kubespray_tag(repo_url, tag_name, base_path="./"):
    """
    Clone a specific kubespray tag from a Git repository.

    Parameters:
        repo_url (str): Repository URL.
        tag_name (str): Tag to be cloned.
        base_path (str): Directory to clone into.

    Raises:
        GitOperationError: If the clone fails.
    """
    dir_name = kubespray_dir_name(tag_name)
    clone_to_path = os.path.join(base_path, dir_name)

    if os.path.exists(clone_to_path):
        logger.warning("Directory '%s' already exists. Skipping.", clone_to_path)
        return

    try:
        logger.info("Cloning tag '%s' from %s", tag_name, repo_url)
        repo = Repo.clone_from(repo_url, clone_to_path)
        repo.git.checkout(tag_name)
        logger.info("Cloned to '%s' at tag '%s'", clone_to_path, tag_name)
    except Exception as e:
        logger.error("Failed to clone %s: %s", tag_name, str(e))
        raise GitOperationError(f"Failed to clone tag: {str(e)}") from e


def delete_kubespray_tag(tag_name, base_path="./"):
    """
    Delete a cloned kubespray tag directory.

    Parameters:
        tag_name (str): Tag name used to form the directory name.
        base_path (str): Parent path of the kubespray directory.

    Raises:
        DirectoryOperationError: If the directory deletion fails.
    """
    dir_name = kubespray_dir_name(tag_name)
    dir_to_delete = os.path.join(base_path, dir_name)

    if os.path.exists(dir_to_delete):
        try:
            shutil.rmtree(dir_to_delete)
            logger.info("Deleted '%s'", dir_to_delete)
        except OSError as e:
            logger.error("Error deleting %s: %s", dir_to_delete, str(e))
            raise DirectoryOperationError(f"Error deleting directory: {str(e)}") from e
    else:
        logger.warning("Directory '%s' does not exist. Nothing to delete.", dir_to_delete)


def clone_latest_tag(kube_version, base_path, arch, n_latest=3):
    """
    Clone the latest kubespray tag that supports the given Kubernetes version.

    Parameters:
        kube_version (str): Desired Kubernetes version.
        base_path (str): Path to clone into.
        arch (str): Architecture to check compatibility.
        n_latest (int): Number of latest tags to try.

    Returns:
        str|None: Compatible tag name if found, else None.

    Raises:
        GitOperationError: If clone fails or no matching tag is found.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        repo = Repo.clone_from(repo_url, temp_dir)
        repo.git.fetch('--tags')

        valid_tags = []
        for tag in repo.tags:
            try:
                version = Version(tag.name.lstrip('v'))
                valid_tags.append((version, tag))
            except InvalidVersion:
                continue

        valid_tags.sort(key=lambda t: t[0], reverse=True)

        for i, (_, tag) in enumerate(valid_tags[:n_latest]):
            tag_name = tag.name
            logger.info("Checking tag %s (%d/%d)", tag_name, i + 1, n_latest)

            dir_name = kubespray_dir_name(tag_name)
            clone_to_path = os.path.join(base_path, dir_name)

            if not os.path.exists(clone_to_path):
                clone_kubespray_tag(repo_url, tag_name, base_path)

            if is_version_within_range(clone_to_path, kube_version, arch):
                logger.info("Found compatible tag %s for %s", tag_name, kube_version)
                return tag_name
            logger.info("Tag %s doesn't support %s", tag_name, kube_version)

        logger.error("No compatible tag found for %s in %d latest tags", kube_version, n_latest)
        return None

    except Exception as e:
        logger.error("Error in clone_latest_tag: %s", str(e))
        raise GitOperationError(f"Error cloning latest tag: {str(e)}") from e
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def clone_kubespray(repo_url, tag_name, clone_to_path):
    """
    Clone the kubespray repository at a specific tag.

    Parameters:
        repo_url (str): URL of the Git repository.
        tag_name (str): Tag to checkout after cloning.
        clone_to_path (str): Directory path to clone the repository into.

    Raises:
        GitOperationError: If cloning or checkout fails.
    """
    try:
        logger.info("Cloning repository %s with tag %s to %s", repo_url, tag_name, clone_to_path)
        repo = Repo.clone_from(repo_url, clone_to_path)
        repo.git.checkout(tag_name)
        logger.info("Repository cloned to %s with tag %s", clone_to_path, tag_name)
    except Exception as e:
        logger.error("An error occurred while cloning the repository: %s", str(e))
        raise GitOperationError(f"Error cloning repository: {str(e)}") from e


# ============================================================================================
#                              Parse image list to process kubespary images
# ============================================================================================
def extract_image_name_and_tag(url):
    """Extract image name and tag from URL"""
    try:
        parsed_url = urlparse(url)
        path = parsed_url.path
        filename = os.path.basename(path)

        if ':' in filename:
            image_name, image_tag = filename.split(':')
        else:
            image_name, image_tag = filename, 'latest'

        return image_name, image_tag
    except Exception as e:
        logger.error(f"Error extracting image name and tag from {url}: {str(e)}")
        raise InvalidInputError(f"Error parsing image URL: {str(e)}")

def get_k8s_data(k8_file_type, k8_file_path, package_types):
    """Get Kubernetes data from JSON file"""
    try:
        filetype_inv = {
            "rpm": [],
            "deb": [],
            "tarball": [],
            "image": [],
            "manifest": [],
            "pip_module": [],
            "git": []

        }
        error_data = []

        if k8_file_type and k8_file_type not in package_types:
            logger.error(f"Not valid package type: {k8_file_type}")
            raise InvalidInputError(f"Invalid package type: {k8_file_type}")

        k8_json_data = load_json(k8_file_path, 'r')
        if not k8_json_data:
            raise FileOperationError(f"Failed to load JSON data from {k8_file_path}")

        for package_info in k8_json_data['k8s']['cluster']:
            package_type = package_info.get("type")
            package_name = package_info.get("package")

            if package_type not in package_types:
                logger.warning(f"Package type {package_type} for package {package_name} is not in the allowed types: {package_types}.")
                error_data.append({package_type: package_info})

            filetype_inv[package_type].append(package_info)

        if len(error_data) != 0:
            logger.error("Found packages with invalid types")
            return False

        if k8_file_type:
            return filetype_inv[k8_file_type]

        return filetype_inv
    except Exception as e:
        logger.error(f"Error processing k8s data: {str(e)}")
        raise FileOperationError(f"Error processing k8s data: {str(e)}")

def extract_version_from_url(url):
    """Extract version from URL using a single optimized regex pattern"""
    try:
        # Combined optimized regex pattern
        pattern = r'(?:v|/|-|_|\.)([\d]+(?:\.[\d]+)+)(?=[/-_.]|$)'


        helm_match = re.search(r'v(\d+\.\d+\.\d+)', url)
        if helm_match:
            return helm_match.group(1)

        match = re.search(pattern, url)

        if match:
            version = match.group(1).strip('.')
            logger.debug(f"Extracted version {version} from {url}")
            return version

        logger.warning(f"Could not extract version from URL: {url}")
        return None
    except Exception as e:
        logger.error(f"Error extracting version from URL {url}: {str(e)}")
        raise InvalidInputError(f"Error extracting version from URL: {str(e)}")

def print_not_found_packages(not_found_packages):
    """Print packages not found in a pretty table"""
    try:
        table = PrettyTable()
        table.field_names = ["package_name", "url"]

        for package in not_found_packages:
            package_name = package.get('package')
            url = package.get('url')
            table.add_row([package_name, url])

        logger.info("\nPackages not found:\n" + str(table))
    except Exception as e:
        logger.error(f"Error printing not found packages: {str(e)}")
        raise

def print_not_found_images(not_found_images):
    """Print images not found in a pretty table"""
    try:
        table = PrettyTable()
        table.field_names = ["package_name", "tag"]

        for package in not_found_images:
            package_name = package.get('package').split('/')[-1]
            tag = package.get('tag')
            table.add_row([package_name, tag])

        logger.info("\nImages not found:\n" + str(table))
    except Exception as e:
        logger.error(f"Error printing not found images: {str(e)}")
        raise

def process_file_list(files_list_path, filetype_inv, final_k8s_data):
    """Process file list and update tarball information"""
    try:
        file_type = "tarball"
        updated_packages = []

        files_list = load_file(files_list_path, 'r')
        if not files_list:
            raise FileOperationError(f"Failed to load file list from {files_list_path}")

        tarball_count = 0

        for url in files_list:
            url_split_data = url.split('/')

            if 'gvisor' in url_split_data:
                continue

            if 'archive' in url_split_data:
                package_name = url_split_data[url_split_data.index('archive')-1] + '.archive-' + url_split_data[-1].replace('.tar.gz', '').replace('.tgz', '')
            else:
                package_name = url_split_data[-1].replace('.tar.gz', '').replace('.tgz', '')

            if '-' in package_name:
                search_name = package_name.split('-')[0]
            else:
                search_name = package_name

            version = extract_version_from_url(url)
            if not version:
                logger.warning(f"Could not extract version from URL: {url}")
                continue

            for item in filetype_inv:
                if item.get('package').split('-')[0] == search_name:
                    item['url'] = url
                    item['package'] = f"{search_name}-v{version}"
                    updated_packages.append({
                        'package': item['package'],
                        'type': 'tarball',
                        'url': url
                    })
                    tarball_count += 1
                    break

        logger.info(f"Processed {tarball_count} tarballs from {len(filetype_inv)} expected")

        final_k8s_data["k8s"]["cluster"].extend(filetype_inv)
        diff_in_list1 = [item for item in filetype_inv if item not in updated_packages]

        if diff_in_list1:
            print_not_found_packages(diff_in_list1)

        return True
    except Exception as e:
        logger.error(f"Error processing file list: {str(e)}")
        raise FileOperationError(f"Error processing file list: {str(e)}")

def process_image_list(images_list_path, filetype_inv, final_k8s_data):
    """Process image list and update image information"""
    try:
        file_type = "image"
        updated_images = []

        images_list = load_file(images_list_path, 'r')
        if not images_list:
            raise FileOperationError(f"Failed to load image list from {images_list_path}")

        image_count = 0
        images_list_dict = {}

        for image in images_list:
            image_name, image_tag = extract_image_name_and_tag(image)
            images_list_dict[image_name] = image_tag




        for item in filetype_inv:
            image_name = item["package"].split('/')[-1]

            if image_name in images_list_dict:
                if item["package"] in SKIP_PACKAGES:
                    continue
                item["tag"] = images_list_dict[image_name]
                updated_images.append({
                    'package': item['package'],
                    'tag': item['tag'],
                    'type': 'image'
                })
                image_count += 1

        logger.info(f"Processed {image_count} images from {len(filetype_inv)} expected")

        final_k8s_data["k8s"]["cluster"].extend(filetype_inv)
        diff_in_list1 = [item for item in filetype_inv if item not in updated_images]

        if diff_in_list1:
            print_not_found_images(diff_in_list1)

        return True
    except Exception as e:
        logger.error(f"Error processing image list: {str(e)}")
        raise FileOperationError(f"Error processing image list: {str(e)}")

def generate_k8_jsons_for_version(kube_version, base_path, repo_url, package_types, k8s_json_path, arch, n_latest, mode="clone"):
    """
    Generates a JSON file for a specific Kubernetes version by cloning a kubespray tag, updating YAML variables, and processing package lists.

    Parameters:
        kube_version (str): The Kubernetes version to generate the JSON file for.
        base_path (str): The base path for cloning and processing.
        repo_url (str): The URL of the kubespray repository.
        package_types (list): A list of package types to process.
        k8s_json_path (str): The path to the k8s JSON file.
        arch (str): The architecture to use for processing.
        n_latest (int): The number of latest branches to process.
        mode (str): The mode of operation (clone, delete, both). Defaults to "clone".

    Returns:
        bool: True if the JSON file was successfully generated, False otherwise.
    """
    try:
        updates = {"kube_version": kube_version}
        update_arch = {"image_arch": arch}

        final_k8s_data = {"k8s": {"cluster": []}}
        output_k8s_path = "base_path + /{kube_version}"
        logger.info("============================ start ============================")
        tag_name = clone_latest_tag(kube_version,  base_path, arch, n_latest)
        if tag_name is None:
            error_msg = f"Did not get the required tag for version {kube_version}"
            logger.error(error_msg)
            raise GitOperationError(error_msg)

        logger.info(f"Cloning the tag: {tag_name}")
        logger.info("============================= end =============================")

        # Construct necessary paths
        clone_path = os.path.join(base_path, f'kubespray-{tag_name}')
        temp_output_path = os.path.join(base_path, kube_version)
        k8inventory_script = os.path.join(clone_path, "contrib/offline/generate_list.sh")

        # Commands to execute
        command = {
            "generate_templates": f"bash {k8inventory_script} ",
            "copy_files": f"cp -r {clone_path}/contrib/offline/temp {temp_output_path}"
        }


        if not verify_file_exists(k8inventory_script):
            error_msg = f"Required script missing: {k8inventory_script}"
            logger.error(error_msg)
            raise FileOperationError(error_msg)

        # update_yaml_path = os.path.join(clone_path, defaults_dir)

        updated , old_value = update_yaml_variables(clone_path, updates)

        revert_update = {"kube_version": old_value}

        if not updated:
            error_msg = f"Failed to update YAML variables in {clone_path}"
            logger.error(error_msg)
            raise FileOperationError(error_msg)

        updated_arch = update_arch_variables(clone_path, update_arch)

        if not updated_arch:
            error_msg = f"Failed to update arch YAML variables in {clone_path}"
            logger.error(error_msg)
            raise FileOperationError(error_msg)


        if not execute_command(command["generate_templates"]):
            error_msg = "Failed to execute generate_list.sh"
            logger.error(error_msg)
            raise CommandExecutionError(error_msg)

        if not execute_command(command["copy_files"]):
            error_msg = "Failed to copy generated inventory files"
            logger.error(error_msg)
            raise CommandExecutionError(error_msg)

        # Load generated package lists
        files_list_path = os.path.join(temp_output_path, "files.list")
        images_list_path = os.path.join(temp_output_path, "images.list")

        updated , old_value = update_yaml_variables(clone_path, revert_update)
        logger.info(f"Reverted the version in yaml file with {revert_update}")
        if not updated:
            error_msg = f"Failed to update old YAML variables in {clone_path}"
            logger.error(error_msg)
            raise FileOperationError(error_msg)

        # Process different package types
        for type_ in SKIP_TYPES:
            try:
                packages = get_k8s_data(type_, k8s_json_path, package_types)
                final_k8s_data["k8s"]["cluster"].extend(packages)
            except Exception as e:
                logger.warning(f"Error processing {type_} packages: {str(e)}")
                continue

        # Process tarballs and images
        try:
            tarballs = get_k8s_data("tarball", k8s_json_path, package_types)
            process_file_list(files_list_path, tarballs, final_k8s_data)
        except Exception as e:
            logger.error(f"Error processing tarballs: {str(e)}")
            raise

        try:
            images = get_k8s_data("image", k8s_json_path, package_types)
            process_image_list(images_list_path, images, final_k8s_data)
        except Exception as e:
            logger.error(f"Error processing images: {str(e)}")
            raise

        if not os.path.isdir(temp_output_path):
            error_msg = f"Output directory does not exist: {kube_version}"
            logger.error(error_msg)
            raise DirectoryOperationError(error_msg)

        output_json_path = os.path.join(temp_output_path, f"k8s_{kube_version}.json")
        write_json(output_json_path, final_k8s_data)

        logger.info(f"Successfully generated JSON for version {kube_version}")
        return True
    except Exception as e:
        logger.error(f"Failed to generate JSON for version {kube_version}: {str(e)}")
        raise

# ============================================================================================
# Ansible Entry Point
# ============================================================================================
def run_module():
    """
    Runs an Ansible module to process Kubernetes versions.

    Parameters:
        kube_versions (list): List of Kubernetes versions to process.
        base_path (str): Base path for processing.
        repo_url (str): Repository URL for cloning.
        package_types (list): List of package types to process.
        k8s_json_path (str): Path to the k8s JSON file.
        log_dir (str): Directory for logging.
        arch (str): Architecture for processing.
        n_latest (int): The number of latest branches to process.
        mode (str): Mode of operation (clone, delete, both).

    Returns:
        dict: Dictionary containing the results of processing each version.
    """
    module_args = {
        "kube_versions": {"type": "list", "required": True},
        "base_path": {"type": "str", "required": True},
        "repo_url": {"type": "str", "required": True},
        "package_types": {"type": "list", "required": True},
        "k8s_json_path": {"type": "str", "required": True},
        "log_dir": {"type": "str", "required": True},
        "arch": {"type": "str", "required": True},
        "n_latest": {"type": "int", "default": 2},
        "mode": {
            "type": "str", 
            "choices": ["clone", "delete", "both"], 
            "default": "clone"
        }
    }

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=False
    )

    try:
        global kube_versions, base_path, repo_url, package_types, k8s_json_path, log_dir, arch, n_latest, mode
        global logger
        logger = setup_logging(module.params['log_dir'])
        kube_versions = module.params['kube_versions']
        base_path = module.params['base_path']
        repo_url = module.params['repo_url']
        package_types = set(module.params['package_types'])
        k8s_json_path = module.params['k8s_json_path']
        log_dir = module.params['log_dir']
        arch = module.params['arch']
        n_latest = module.params['n_latest']
        mode = module.params['mode']

        logger.info("Starting kubespray processor with parameters:")
        logger.info("Kubernetes versions: %s", kube_versions)
        logger.info("Base path: %s", base_path)
        logger.info("Repository URL: %s", repo_url)
        logger.info("Package types: %s", package_types)
        logger.info("K8s JSON path: %s", k8s_json_path)
        logger.info("K8s log directory: %s", log_dir)
        logger.info("Number of latest branches: %s", n_latest)
        logger.info("Architecture: %s", arch)
        logger.info("Mode: %s", mode)

        version_results = {}
        for version in kube_versions:
            try:
                logger.info(f"Processing Kubernetes version: {version}")
                result = generate_k8_jsons_for_version(
                    version, base_path, repo_url, package_types, k8s_json_path, arch, n_latest, mode
                )
                version_results[version] = "Success" if result else "Failed"

                if not result:
                    module.fail_json(msg=f"Failed to generate JSON for version {version}", version_results=version_results)
                    
            except Exception as e:
                logger.error(f"Error processing version {version}: {str(e)}")
                version_results[version] = f"Error: {str(e)}"
                module.fail_json(msg=f"Error processing version {version}: {str(e)}", version_results=version_results)

        module.exit_json(changed=True, msg="All versions processed successfully.", version_results=version_results)

    except Exception as e:
        logger.error(f"Unexpected error in run_module: {str(e)}")
        module.fail_json(msg=f"Unexpected error: {str(e)}")

if __name__ == '__main__':
    run_module()
