"""Helper module to connect a CouchDB Kube StatefulSet

This script grabs the list of pod names behind the headless service
associated with our StatefulSet and feeds those as `couchdb@FQDN`
nodes to mem3.

As a final step - on just one of the nodes - the cluster finalize is triggered.
For that part to happen we need username and password.
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
        while resp.status_code != 201 and resp.status_code != 409:
            print('Waiting for _nodes DB to be created.',uri,'returned', resp.status_code, resp.json(), flush=True)
            time.sleep(5)
            if creds[0] and creds[1]:
                resp = requests.put(uri, data=json.dumps(doc), auth=creds)
            else:
                resp = requests.put(uri, data=json.dumps(doc))
        if resp.status_code == 201:
            print('Adding CouchDB cluster node', name, "to this pod's CouchDB. Response code:", resp.status_code ,flush=True)

# Compare (json) objects - order does not matter. Credits to:
# https://stackoverflow.com/a/25851972
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# Run action:finish_cluster on one (and only one) CouchDB cluster node
def finish_cluster(names):
    # The HTTP POST to /_cluster_setup should be done to
    # one (and only one) of the CouchDB cluster nodes.
    # Search for "setup coordination node" in
    # http://docs.couchdb.org/en/stable/cluster/setup.html
    # Given that K8S has a standardized naming for the pods:
    # https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#pod-identity
    # we can make sure that this code runs
    # on the "first" pod only with this hack:
    if (os.getenv("HOSTNAME").endswith("-0")):
        creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
        print("== Get the cluster up and running ===")
        setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={"action": "finish_cluster"},  auth=creds)
        print ('\tRequest: POST http://127.0.0.1:5984/_cluster_setup , payload {"action": "finish_cluster"}')
        print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())
        if (setup_resp.status_code == 201):
            print ("\tSweet! Just a final check for the logs...")
            setup_resp=requests.get("http://127.0.0.1:5984/_cluster_setup",  auth=creds)
            print ('\tRequest: GET http://127.0.0.1:5984/_cluster_setup')
            print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())
            print("Time to relax!")
        else:
            print('Ouch! Failed the final step: http://127.0.0.1:5984/_cluster_setup returned {0}'.format(setup_resp.status_code))
    else:
        print("This pod is intentionally skipping the call to http://127.0.0.1:5984/_cluster_setup")

@backoff.on_exception(
    backoff.expo,
    requests.exceptions.ConnectionError,
    max_tries=10
)
# Check if the _membership API on all (known) CouchDB nodes have the same values.
# Returns true if same. False in any other situation.
def are_nodes_in_sync(names):
    # Make sure that ALL (known) CouchDB cluster peers have been
    # have the same _membership data.Use "this" nodes memebership as
    # "source"
    local_membership_uri = "http://127.0.0.1:5984/_membership"
    print ("Fetching CouchDB node mebership from this pod: {0}".format(local_membership_uri),flush=True)
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))   
    if creds[0] and creds[1]:
        local_resp = requests.get(local_membership_uri,  auth=creds)
    else:
        local_resp = requests.get(local_membership_uri)

    # If any difference is found - set to true
    is_different = False

    # Step through every peer. Ensure they are "ready" before progressing.
    for name in names:
        print("Probing {0} for cluster membership".format(name))
        remote_membership_uri = "http://{0}:5984/_membership".format(name)
        if creds[0] and creds[1]:
            remote_resp = requests.get(remote_membership_uri,  auth=creds)
        else:
            remote_resp = requests.get(remote_membership_uri)

        if len(names) < 2:
            # Minimum 2 nodes to form cluster!
            is_different = True   

        # Compare local and remote _mebership data. Make sure the set
        # of nodes match. This will ensure that the remote nodes
        # are fully primed with nodes data before progressing with
        # _cluster_setup
        if (remote_resp.status_code == 200) and (local_resp.status_code == 200):
            if len(local_resp.json()) < 2:
                # Minimum 2 nodes to form cluster!
                is_different = True
            if ordered(local_resp.json()) != ordered(remote_resp.json()):
                is_different = True
                print ("Fetching CouchDB node mebership from this pod: {0}".format(local_membership_uri),flush=True)
                records_in_local_but_not_in_remote = set(local_resp.json().cluster_nodes) - set(remote_resp.json().cluster_nodes)
                records_in_remote_but_not_in_local = set(remote_resp.json().cluster_nodes) - set(local_resp.json().cluster_nodes)
                if records_in_local_but_not_in_remote:
                    print ("Cluster members in {0} not yet present in {1}: {2}".format(os.getenv("HOSTNAME"), name.split(".",1)[0], records_in_local_but_not_in_remote))
                if records_in_remote_but_not_in_local:
                    print ("Cluster members in {0} not yet present in {1}: {2}".format(name.split(".",1)[0], os.getenv("HOSTNAME"), records_in_remote_but_not_in_local))
        else:
            is_different = True
 
        if (remote_resp.status_code == 200) and (local_resp.status_code == 200):
            print("local: ",local_resp.json().cluster_nodes)
            print("remote: ",remote_resp.json().cluster_nodes)
        print('returnerar', not is_different)
    return not is_different

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    peer_names = discover_peers(construct_service_record())
    connect_the_dots(peer_names)
    print("Got the following peers' fqdm from DNS lookup:",peer_names,flush=True)

    # loop until all CouchDB nodes discovered
    while not are_nodes_in_sync(peer_names):
        time.sleep(5)
        peer_names = discover_peers(construct_service_record())
        connect_the_dots(peer_names)
    print('Cluster membership populated!')
    
    if (os.getenv("COUCHDB_USER") and os.getenv("COUCHDB_PASSWORD")):
        finish_cluster(peer_names)
    else:
        print ('Skipping cluster final setup. Username and/or password not provided')
    sleep_forever()
