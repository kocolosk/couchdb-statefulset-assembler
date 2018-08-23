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

# Run action:enable_cluster on every CouchDB cluster node
def enable_cluster(nr_of_peers):
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))   
    if creds[0] and creds[1]:
        headers = {'Content-type': 'application/json'}
        print ("=== Enabling cluster mode ===")
        # http://docs.couchdb.org/en/stable/cluster/setup.html
        payload = {}
        payload['action'] = 'enable_cluster'
        payload['bind_address'] = '0.0.0.0'
        payload['username'] = creds[0]
        payload['password'] = creds[1]
        payload['node_count'] = nr_of_peers
        setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json.dumps(payload),  auth=creds, headers=headers)
        payload['password'] = "**masked**"
        print ("\tRequest: POST http://127.0.0.1:5984/_cluster_setup , payload:",json.dumps(payload))
        print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

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
    if (os.getenv("HOSTNAME").endswith("-0")):
        creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))

        headers = {'Content-type': 'application/json'}
        print ("=== Adding nodes to CouchDB cluster via the “setup coordination node” ===")
        for name in names:
            # Exclude "this" pod
            if (name.split(".",1)[0] != os.getenv("HOSTNAME")):
                # action: enable_cluster
                payload = {}
                payload['action'] = 'enable_cluster'
                payload['bind_address'] = '0.0.0.0'
                payload['username'] = creds[0]
                payload['password'] = creds[1]
                payload['port'] = 5984
                payload['node_count'] = len(names)
                payload['remote_node'] = name
                payload['remote_current_user'] = creds[0]
                payload['remote_current_password'] = creds[1]
                setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json.dumps(payload),  auth=creds, headers=headers)
                payload['password'] = "**masked**"
                payload['remote_current_password'] = "**masked**"
                print ("\tRequest: POST http://127.0.0.1:5984/_cluster_setup , payload:",json.dumps(payload))
                print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

                # action: add_node
                payload = {}
                payload['action'] = 'add_node'
                payload['username'] = creds[0]
                payload['password'] = creds[1]
                payload['port'] = 5984
                payload['host'] = name
                setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json.dumps(payload),  auth=creds, headers=headers)
                payload['password'] = "**masked**"
                print ("\tRequest: POST http://127.0.0.1:5984/_cluster_setup , payload:",json.dumps(payload))
                print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

        # Make sure that ALL CouchDB cluster peers have been
        # primed with _nodes data before /_cluster_setup
        # Use the _membership of "this" pod's CouchDB as reference
        local_membership_uri = "http://127.0.0.1:5984/_membership"
        print ("Fetching CouchDB node mebership from this pod: {0}".format(local_membership_uri),flush=True)
        if creds[0] and creds[1]:
            local_resp = requests.get(local_membership_uri,  auth=creds)
        else:
            local_resp = requests.get(local_membership_uri)
        # Step through every peer. Ensure they are "ready" before progressing.
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
            print("Node {0} has all node members in place!".format(name))

            print('CouchDB cluster peer {} added to "setup coordination node"'.format(name))
        # At this point ALL peers have _nodes populated. Finish the cluster setup!

        print("== Creating default databases ===")
        setup_resp=requests.put("http://127.0.0.1:5984/_users",  auth=creds)
        print ("\tRequest: PUT http://127.0.0.1:5984/_users")
        print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

        setup_resp=requests.put("http://127.0.0.1:5984/_replicator",  auth=creds)
        print ("\tRequest: PUT http://127.0.0.1:5984/_replicator")
        print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

        setup_resp=requests.put("http://127.0.0.1:5984/_global_changes",  auth=creds)
        print ("\tRequest: PUT http://127.0.0.1:5984/_global_changes")
        print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

        setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={"action": "finish_cluster"},  auth=creds)
        if (setup_resp.status_code == 201):
            print("== CouchDB cluster setup done! ===")
            print ('\tRequest: POST http://127.0.0.1:5984/_cluster_setup , payload {"action": "finish_cluster"}')
            print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())
            setup_resp=requests.get("http://127.0.0.1:5984/_cluster_setup",  auth=creds)
            print ('\tRequest: GET http://127.0.0.1:5984/_cluster_setup')
            print ("\t\tResponse:", setup_resp.status_code, setup_resp.json())

            print("Time to relax!")
        else:
            print('Ouch! Failed the final step: http://127.0.0.1:5984/_cluster_setup returned {0}'.format(setup_resp.status_code))
    else:
        print("This pod is intentionally skipping the call to http://127.0.0.1:5984/_cluster_setup")

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    peer_names = discover_peers(construct_service_record())
    print("Got the following peers' fqdm from DNS lookup:",peer_names,flush=True)
    if (os.getenv("COUCHDB_USER") and os.getenv("COUCHDB_PASSWORD")):
        enable_cluster(len(peer_names))
    connect_the_dots(peer_names)
    print('Cluster membership populated!')
    if (os.getenv("COUCHDB_USER") and os.getenv("COUCHDB_PASSWORD")):
        finish_cluster(peer_names)
    else:
        print ('Skipping cluster setup. Username and/or password not provided')
    sleep_forever()
