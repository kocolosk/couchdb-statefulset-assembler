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
    result = [rdata.target.to_text()[:-1] for rdata in answers]
    print("\t| Got the following peers' fqdm from DNS lookup:",result,flush=True)
    return result

def backoff_hdlr(details):
    print ("Backing off {wait:0.1f} seconds afters {tries} tries "
           "calling function {target} with args {args} and kwargs "
           "{kwargs}".format(**details))

def connect_the_dots(names):

    # Ordinal Index: For a StatefulSet with N replicas, each Pod in the StatefulSet
    # will be assigned an integer ordinal, from 0 up through N-1, that is unique over the Set.
    # Approach: get the Ordinal index of this pod and make sure that the list of name
    # include all those ordinals.
    # By looking at this pods Ordinal, make sure that all DNS records for
    # pods having lesser Ordinal are found before adding any nodes to CouchDB.
    # This is done to PREVENT the following case:
    # (1) POD with ordnial 1 get DNS records for ordinal 1 and 2.
    # (2) POD with ordinal 2 get DNS records for ordinal 1 and 2.
    # (3) The are_nodes_in_sync function will give green light and
    #     no further discovery is taken place.
    # (4) Pod with ordinal 0 will get are_nodes_in_sync=true and cluster
    #     setup will fail.

    ordinal_of_this_pod = int(os.getenv("HOSTNAME").split("-")[-1])
    expected_ordinals = set(range(0, ordinal_of_this_pod))
    found_ordinals = set([])
    for name in names:
        # Get the podname, get the stuff after last - and convert to int
        found_ordinals.add(int(name.split(".",1)[0].split("-")[-1]));

    print("expected_ordinals",expected_ordinals)
    print("found ordnials",found_ordinals)

    # Are there expected ordinals that are not part of the found ordinals?
    if( expected_ordinals - found_ordinals):
        print ('Expected to get at least the following pod ordinal(s)', expected_ordinals - found_ordinals, 'among the DNS records. Will retry.')
    else:
        creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
        for name in names:
            uri = "http://127.0.0.1:5986/_nodes/couchdb@{0}".format(name)
            doc = {}
            print('Adding CouchDB cluster node', name, "to this pod's CouchDB.")
            print ('\t| Request: PUT',uri)
            try:
                if creds[0] and creds[1]:
                    resp = requests.put(uri, data=json.dumps(doc), auth=creds)
                else:
                    resp = requests.put(uri, data=json.dumps(doc))
                print ("\t| Response:", setup_resp.status_code, setup_resp.json(),flush=True)
            except requests.exceptions.ConnectionError:
                print ('\t| Connection failure. CouchDB not responding. Will retry.')

# Compare (json) objects - order does not matter. Credits to:
# https://stackoverflow.com/a/25851972
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

# Call action:finish_cluster on one (and only one) CouchDB cluster node
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
        print ('\t| Request: POST http://127.0.0.1:5984/_cluster_setup , payload {"action": "finish_cluster"}')
        print ("\t|\tResponse:", setup_resp.status_code, setup_resp.json())
        if (setup_resp.status_code == 201):
            print ("\t| Sweet! Just a final check for the logs...")
            setup_resp=requests.get("http://127.0.0.1:5984/_cluster_setup",  auth=creds)
            print ('\t| Request: GET http://127.0.0.1:5984/_cluster_setup')
            print ("\t|\tResponse:", setup_resp.status_code, setup_resp.json())
            print("Time to relax!")
        else:
            print('Ouch! Failed the final step finalizing the cluster.')
    else:
        print('This pod is intentionally skipping the POST to http://127.0.0.1:5984/_cluster_setup {"action": "finish_cluster"}')


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
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))   
    if creds[0] and creds[1]:
        local_resp = requests.get(local_membership_uri,  auth=creds)
    else:
        local_resp = requests.get(local_membership_uri)

    # If any difference is found - set to true
    not_ready = False

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
            not_ready = True
            print("\t| Need at least 2 DNS records to start with. Got ",len(names))

        # Compare local and remote _mebership data. Make sure the set
        # of nodes match. This will ensure that the remote nodes
        # are fully primed with nodes data before progressing with
        # _cluster_setup
        if (remote_resp.status_code == 200) and (local_resp.status_code == 200):
            if ordered(local_resp.json()) == ordered(remote_resp.json()):
                print ("\t| In sync!")
            else:
                not_ready = True
                # Rest is for logging only...
                records_in_local_but_not_in_remote = set(local_resp.json()['cluster_nodes']) - set(remote_resp.json()['cluster_nodes'])
                if records_in_local_but_not_in_remote:
                    print ("\t| Cluster members in {0} not yet present in {1}: {2}".format(os.getenv("HOSTNAME"), name.split(".",1)[0], records_in_local_but_not_in_remote))
                records_in_remote_but_not_in_local = set(remote_resp.json()['cluster_nodes']) - set(local_resp.json()['cluster_nodes'])
                if records_in_remote_but_not_in_local:
                    print ("\t| Cluster members in {0} not yet present in {1}: {2}".format(name.split(".",1)[0], os.getenv("HOSTNAME"), records_in_remote_but_not_in_local))

            # Cover the case where local pod has 1 record only
            if len(local_resp.json()['cluster_nodes']) < 2:
                # Minimum 2 nodes to form cluster!
                not_ready = True
                print("\t| Need at least 2 cluster nodes in the _membership of pod",os.getenv("HOSTNAME"))
        else:
            not_ready = True
    return not not_ready

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    peer_names = discover_peers(construct_service_record())
    connect_the_dots(peer_names)

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
