# Copyright 2018 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from __future__ import absolute_import, print_function, unicode_literals

import errno
import os.path
import shutil
import subprocess

from charmhelpers.core import (
    hookenv,
    host,
    templating,
    )
from charmhelpers.fetch import apt_install
from charmhelpers.payload import archive
from charms.layer import status
from charms.reactive import endpoint_from_name
import six
import yaml


def base_dir():
    return hookenv.config()['base_dir']


def payloads_dir():
    return os.path.join(base_dir(), hookenv.application_name(), 'payloads')


def code_dir():
    return os.path.join(base_dir(), hookenv.application_name(), 'code')


def venv_dir():
    return os.path.join(code_dir(), 'env')


def logs_dir():
    return os.path.join(base_dir(), 'logs')


def data_dir():
    return os.path.join(base_dir(), 'data')


def run_dir():
    return os.path.join(base_dir(), hookenv.application_name(), 'run')


@hookenv.cached
def data_mount_unit():
    return subprocess.check_output(
        ['systemd-escape', '--path', data_dir()],
        universal_newlines=True).rstrip('\n') + '.mount'


def keys_dir():
    return os.path.join(base_dir(), 'keys')


def nrpe_dir():
    return os.path.join(base_dir(), hookenv.application_name(), 'nrpe')


def user():
    return hookenv.config()['user']


def user_id():
    return hookenv.config()['user_id']


def group():
    return hookenv.config()['group']


def group_id():
    return hookenv.config()['group_id']


def ensure_user(username, groupname, uid=None, gid=None):
    if not host.user_exists(username):
        hookenv.log('Creating {} user and group'.format(username))
        password = host.pwgen()
        if not host.group_exists(groupname):
            host.add_group(groupname, gid=gid)
        host.adduser(username, password, primary_group=groupname, uid=uid)


def ensure_directories():
    for dir in (base_dir(), payloads_dir()):
        host.mkdir(dir, perms=0o755)
    for dir in (logs_dir(), data_dir(), keys_dir(), run_dir()):
        host.mkdir(dir, owner=user(), group=group(), perms=0o755)


def get_swift_creds(config):
    return {
        'user': config['swift_username'],
        'project': config['swift_tenant_name'],
        'password': config['swift_password'],
        'authurl': config['swift_auth_url'],
        'region': config['swift_region_name'],
        'storageurl': config['swift_storage_url'],
        }


def swift_base_cmd(**swift_creds):
    return [
        'swift',
        '--os-username=' + swift_creds['user'],
        '--os-tenant-name=' + swift_creds['project'],
        '--os-password=' + swift_creds['password'],
        '--os-auth-url=' + swift_creds['authurl'],
        '--os-region-name=' + swift_creds['region'],
        ]


def swift_fetch(source, target, container=None, **swift_creds):
    if swift_creds['user']:
        cmd = swift_base_cmd(**swift_creds) + [
            'download', '--output=' + target, container, source]
    else:
        storage_url = swift_creds['storageurl']
        assert storage_url
        cmd = [
            'wget', '-O', target,
            '%s/%s/%s' % (storage_url, container, source)]
    subprocess.check_call(cmd)


def unlink_force(path):
    """Unlink path, without worrying about whether it exists."""
    try:
        os.unlink(path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


def symlink_force(source, link_name):
    """Create symlink link_name -> source, even if link_name exists."""
    unlink_force(link_name)
    os.symlink(source, link_name)


def install_packages():
    if hookenv.config()['swift_username']:
        apt_install('python-swiftclient', fatal=True)


def install_payload_packages(target_dir):
    system_deps_path = os.path.join(target_dir, 'system-dependencies.txt')
    if not os.path.exists(system_deps_path):
        return
    hookenv.log('Installing system packages required by payload')
    with open(system_deps_path) as system_deps_file:
        system_deps = system_deps_file.read().split()
    apt_install(system_deps, fatal=True)


def install_python_packages(target_dir):
    hookenv.log('Installing Python dependencies')
    subprocess.check_call(
        ['sudo', 'make', '-C', target_dir, 'build',
         'PIP_SOURCE_DIR=%s' % os.path.join(target_dir, 'pip-cache')])


def prune_payloads(keep):
    for entry in os.listdir(payloads_dir()):
        if entry in keep:
            continue
        entry_path = os.path.join(payloads_dir(), entry)
        if os.path.isdir(entry_path):
            hookenv.log('Purging old build in {}'.format(entry_path))
            shutil.rmtree(entry_path)


class PayloadError(Exception):
    pass


def install_payload():
    config = hookenv.config()
    current_build_label = None
    if os.path.islink(code_dir()):
        current_build_label = os.path.basename(os.path.realpath(code_dir()))
    desired_build_label = config['build_label']
    if not desired_build_label:
        raise PayloadError('Build label unset, so cannot deploy code')
    if current_build_label == desired_build_label:
        hookenv.log('Build {} already deployed'.format(desired_build_label))
        return
    hookenv.log('Deploying build {}...'.format(desired_build_label))

    # Copy source archive
    archive_path = os.path.join(
        payloads_dir(), desired_build_label + '.tar.gz')
    object_name = os.path.join(desired_build_label, 'turnip.tar.gz')

    try:
        if config['swift_container_name']:
            swift_creds = get_swift_creds(config)
            swift_container = config['swift_container_name']
            swift_fetch(
                os.path.join('turnip-builds', object_name), archive_path,
                container=swift_container, **swift_creds)
        else:
            resource_path = hookenv.resource_get('turnip')
            if resource_path and os.path.getsize(resource_path):
                with open(resource_path, 'rb') as resource:
                    host.write_file(archive_path, resource.read(), perms=0o644)
            else:
                raise PayloadError('No build available, so cannot deploy code')

        # Unpack source
        target_dir = os.path.join(payloads_dir(), desired_build_label)
        if os.path.isdir(target_dir):
            shutil.rmtree(target_dir)
        archive.extract_tarfile(archive_path, target_dir)
        os.chown(target_dir, 0, 0)
        host.lchownr(target_dir, 'root', 'root')

        install_payload_packages(target_dir)
        install_python_packages(target_dir)

        symlink_force(
            os.path.relpath(target_dir, os.path.dirname(code_dir())),
            code_dir())
        prune_payloads([desired_build_label, current_build_label])
    finally:
        unlink_force(archive_path)


def configure_rsync():
    config = hookenv.config()
    if config['log_hosts_allow']:
        templating.render(
            'turnip-rsync.j2', '/etc/rsync-juju.d/010-turnip.conf',
            config, perms=0o644)
        if not host.service_restart('rsync'):
            raise RuntimeError('Failed to restart rsync')


def install_services():
    install_payload()
    version_info_path = os.path.join(code_dir(), 'turnip', 'version_info.py')
    if os.path.exists(version_info_path):
        version_info_locals = {}
        with open(version_info_path) as version_info_file:
            six.exec_(version_info_file.read(), {}, version_info_locals)
        revision_id = version_info_locals.get('version_info', {}).get(
            'revision_id')
        if revision_id is not None:
            hookenv.application_version_set(revision_id)
    configure_rsync()


def configure_logrotate(config):
    templating.render(
        'logrotate.j2',
        os.path.join('/etc/logrotate.d', hookenv.application_name()),
        config, perms=0o644)


def reload_systemd():
    subprocess.check_call(['systemctl', 'daemon-reload'])


def configure_service(service_name=None):
    config = hookenv.config()
    if service_name is not None:
        context = dict(config)
        context['code_dir'] = code_dir()
        context['data_dir'] = data_dir()
        # XXX cjwatson 2019-02-11: data_mount_unit is only meaningful with
        # layer:turnip-storage, but we include it here for simplicity.
        context['data_mount_unit'] = data_mount_unit()
        context['run_dir'] = run_dir()
        context['venv_dir'] = venv_dir()
        templating.render(
            '{}.service.j2'.format(service_name),
            '/lib/systemd/system/{}.service'.format(service_name),
            context, perms=0o644)
        reload_systemd()
        if host.service_running(service_name):
            host.service_stop(service_name)
        if not host.service_resume(service_name):
            raise RuntimeError('Failed to start {}'.format(service_name))
    port = config.get('port')
    if port is not None:
        hookenv.open_port(port)
    configure_logrotate(config)


def deconfigure_service(service_name):
    host.service_pause(service_name)


def add_nagios_e2e_checks(nagios):
    config = hookenv.config()
    src = os.path.join(hookenv.charm_dir(), 'scripts', 'nrpe')
    dst = nrpe_dir()
    if not os.path.exists(dst):
        os.makedirs(dst)
    shutil.copy2(
        os.path.join(src, 'check_git_refs'),
        os.path.join(dst, 'check_git_refs'))
    nagios_logs_dir = '/var/log/nagios'
    try:
        if not os.path.exists(nagios_logs_dir):
            host.mkdir(
                nagios_logs_dir, owner='nagios', group='nagios', perms=0o755)
    except KeyError:
        hookenv.log(
            'nagios user not set up; {} not created'.format(nagios_logs_dir))
    for i, url in enumerate(config['nagios_e2e_urls'].split()):
        nagios.add_check(
            [os.path.join(nrpe_dir(), 'check_git_refs'), url],
            name='check_turnip_git_refs_{}'.format(i),
            description='Git E2E {}'.format(url),
            context=config['nagios_context'])


def find_git_service(git_services, name):
    for relation in git_services.relations:
        for unit in relation.joined_units:
            data = unit.received_raw
            all_services = data.get('all_services')
            if all_services is not None:
                for service in yaml.safe_load(all_services):
                    if service['service_name'] == name:
                        return data['private-address'], service['service_port']
    return None, None


def publish_website(website, name, port):
    config = hookenv.config()
    server_name = hookenv.local_unit().replace('/', '-')
    server_ip = str(hookenv.unit_private_ip())
    try:
        service_options = yaml.safe_load(
            config.get('haproxy_service_options'))
    except Exception:
        status.blocked('bad haproxy_service_options YAML config')
        return
    server_options = config.get('haproxy_server_options')
    haproxy_services = [{
        'service_name': name,
        'service_host': '0.0.0.0',
        'service_port': port,
        'service_options': service_options,
        'servers': [[server_name, server_ip, port, server_options]],
        }]
    haproxy_services_yaml = yaml.dump(haproxy_services)
    for relation in website.relations:
        ingress_address = website.get_ingress_address(relation.relation_id)
        relation.to_publish_raw.update({
            'hostname': ingress_address,
            'private-address': ingress_address,
            'port': str(port),
            'services': haproxy_services_yaml,
            })


def get_rabbitmq_url():
    rabbitmq = endpoint_from_name('amqp')

    if not rabbitmq.username() or not rabbitmq.password():
        raise AssertionError(
            'get_rabbitmq_url called when amqp relation data incomplete')

    user = "%s:%s" % (rabbitmq.username(), rabbitmq.password())
    vhost = rabbitmq.vhost() if rabbitmq.vhost() else "/"
    return "pyamqp://%s@%s/%s" % (user, rabbitmq.private_address(), vhost)
