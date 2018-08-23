"""Helper module to connect a CouchDB Kube StatefulSet

This script grabs the list of pod names behind the headless service
associated with our StatefulSet and feeds those as `couchdb@FQDN`
nodes to mem3.
"""

import json
import requests
import time
import dns.resolver
import socket
import backoff
import os

def construct_service_record():
    # Drop our Pod's unique identity and replace with '_couchdb._tcp'
    return os.getenv('SRV_RECORD') or '.'.join(['_couchdb', '_tcp'] + socket.getfqdn().split('.')[1:])

@backoff.on_exception(
    backoff.expo,
    dns.resolver.NXDOMAIN,
    max_tries=10
)
def discover_peers(service_record):
    print('Resolving SRV record', service_record, flush=True)
    answers = dns.resolver.query(service_record, 'SRV')
    # Erlang requires that we drop the trailing period from the absolute DNS
    # name to form the hostname used for the Erlang node. This feels hacky
    # but not sure of a more official answer
    return [rdata.target.to_text()[:-1] for rdata in answers]

@backoff.on_exception(
    backoff.expo,
    requests.exceptions.ConnectionError,
    max_tries=10
)
def connect_the_dots(names):
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
    for name in names:
        uri = "http://127.0.0.1:5986/_nodes/couchdb@{0}".format(name)
        doc = {}
        if creds[0] and creds[1]:
            resp = requests.put(uri, data=json.dumps(doc), auth=creds)
        else:
            resp = requests.put(uri, data=json.dumps(doc))
        while resp.status_code != 201:
            print('Waiting for _nodes DB to be created.',uri,'returned', resp.status_code, flush=True)
            time.sleep(5)
            if creds[0] and creds[1]:
                resp = requests.put(uri, data=json.dumps(doc), auth=creds)
            else:
                resp = requests.put(uri, data=json.dumps(doc))
        print('Adding CouchDB cluster node', name, "to this pod's CouchDB", flush=True)

# Compare (json) objects - order does not matter. Credits to:
# https://stackoverflow.com/a/25851972
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# Run action:finish_cluster on one and only one CouchDB cluster node
def finish_cluster(names):
    # The HTTP POST to /_cluster_setup should be done to
    # one (and only one) of the CouchDB cluster nodes.
    # Search for "setup coordination node" in
    # http://docs.couchdb.org/en/stable/cluster/setup.html
    # Given that K8S has a standardized naming for the pods:
    # https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#pod-identity
    # we can make sure that this code runs
    # on the "first" pod only with this hack:
    print('HOSTNAME={0}'.format(os.getenv("HOSTNAME")))
    if (os.getenv("HOSTNAME").endswith("-0")):
        creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
        # Make sure that ALL CouchDB cluster peers have been
        # primed with _nodes data before /_cluster_setup
        # Use the _membership of "this" pod's CouchDB as reference
        local_membership_uri = "http://127.0.0.1:5984/_membership"
        print ("Fetching CouchDB node mebership from this pod: {0}".format(local_membership_uri),flush=True)
        if creds[0] and creds[1]:
            local_resp = requests.get(local_membership_uri,  auth=creds)
        else:
            local_resp = requests.get(local_membership_uri)
        # Step through every peer pod and grab the _membership.
        for name in names:
            print("Probing {0} for cluster membership".format(name))
            remote_membership_uri = "http://{0}:5984/_membership".format(name)
            if creds[0] and creds[1]:
                remote_resp = requests.get(remote_membership_uri,  auth=creds)
            # Compare local and remote _mebership data. Make sure the set
            # of nodes match. This will ensure that the remote nodes
            # are fully primed with nodes data before progressing with
            # _cluster_setup
            while (remote_resp.status_code != 200) or (ordered(local_resp.json()) != ordered(remote_resp.json())):
                # print ("remote_resp.status_code",remote_resp.status_code)
                # print (ordered(local_resp.json()))
                # print (ordered(remote_resp.json()))
                print('Waiting for node {0} to have all node members populated'.format(name),flush=True)
                time.sleep(5)
                if creds[0] and creds[1]:
                    remote_resp = requests.get(remote_membership_uri,  auth=creds)
            # The node in <name> is primed
            # http://docs.couchdb.org/en/stable/cluster/setup.html

            # action: enable_cluster
            payload = '"action": "enable_cluster", "bind_address":"0.0.0.0", "username":"{0}", "password":"{1}", "port": 5984, "node_count":"{2}"  "remote_node": "{3}", "remote_current_user": "{0}", "remote_current_password": "{1}"'.format(creds[0],creds[1],nr_of_peers,name)
            setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={payload},  auth=creds)
            print ("POST to http://127.0.0.1:5984/_cluster_setup returned",setup_resp.status_code,"payload=",payload)

            # action: add_node
            payload = '"action": "add_node", "host":"{0}", "port": 5984, "username": "{1}", "password":"{2}"'.format(name, creds[0],creds[1])
            setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={payload},  auth=creds)
            print ("POST to http://127.0.0.1:5984/_cluster_setup returned",setup_resp.status_code,"payload=",payload)

            print('CouchDB cluster peer {} added to "setup coordination node"'.format(name))
        # At this point ALL peers have _nodes populated. Finish the cluster setup!
        if creds[0] and creds[1]:
            setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={"action": "finish_cluster"},  auth=creds)
        if (setup_resp.status_code == 201):
            print("CouchDB cluster setup done. Time to relax!")
        else:
            print('Ouch! Failed the final step: http://127.0.0.1:5984/_cluster_setup returned {0}'.format(setup_resp.status_code))
    else:
        print("This pod is intentionally skipping the call to http://127.0.0.1:5984/_cluster_setup")

# Run action:enable_cluster on every CouchDB cluster node
def enable_cluster(nr_of_peers):
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))   
    if creds[0] and creds[1]:
        # http://docs.couchdb.org/en/stable/cluster/setup.html
        payload = '"action": "enable_cluster", "bind_address":"0.0.0.0", "{0}": "admin", "{1}":"password", "node_count":"{2}"'.format(creds[0],creds[1],nr_of_peers)
        setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={payload},  auth=creds)
        print ("POST to http://127.0.0.1:5984/_cluster_setup returned",setup_resp.status_code,"payload=",payload)

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    peer_names = discover_peers(construct_service_record())
    print("Got the following peers' fqdm from DNS lookup:",peer_names,flush=True)
    connect_the_dots(peer_names)
    print('Cluster membership populated!')
    if (os.getenv("COUCHDB_USER") and os.getenv("COUCHDB_PASSWORD")):
        enable_cluster(len(peer_names))
        finish_cluster(peer_names)
    else:
        print ('Skipping cluster setup. Username and/or password not provided')
    sleep_forever()
