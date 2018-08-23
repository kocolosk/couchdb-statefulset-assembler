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
    print ('Resolving SRV record', service_record)
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
        while resp.status_code == 404:
            print('Waiting for _nodes DB to be created ...')
            time.sleep(5)
            resp = requests.put(uri, data=json.dumps(doc))
        print('Adding cluster member', name, resp.status_code)

# Compare (json) objects - order does not matter. Credits to:
# https://stackoverflow.com/a/25851972
def ordered(obj):
    if isinstance(obj, dict):
        return sorted((k, ordered(v)) for k, v in obj.items())
    if isinstance(obj, list):
        return sorted(ordered(x) for x in obj)
    else:
        return obj

def finish_cluster(names):
    # The HTTP POST to /_cluster_setup should be done to
    # one (and only one) of the CouchDB cluster nodes.
    # Given that K8S has a standardized naming for the pods:
    # https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#pod-identity
    # we can make sure that this code is only bing run
    # on the "first" pod using this hack:
    if (os.getenv("HOSTNAME").endswith("_0")):
        # Make sure that ALL CouchDB cluster peers have been
        # primed with _nodes data before /_cluster_setup
        creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
        # Use the _members of "this" pod's CouchDB as reference
        local_members_uri = "http://127.0.0.1:5984/_members"
        print ("Fetching node mebership from this pod: {0}".format(local_members_uri))
        if creds[0] and creds[1]:
            local_resp = requests.get(local_members_uri,  auth=creds)
        else:
            local_resp = requests.get(local_members_uri)
        for name in names:
            print("Probing node {0} for _members".format(name))
            if creds[0] and creds[1]:
                remote_resp = requests.get("http://{0}:5984/_members".format(name),  auth=creds)
            else:
                remote_resp = requests.get("http://{0}:5984/_members".format(name))
            while (remote_resp.status_code == 404) or (ordered(local_resp.json()) != ordered(remote_resp.json())):
                print('Waiting for node {0} to have all node members populated'.format(name))
                time.sleep(5)
                if creds[0] and creds[1]:
                    remote_resp = requests.get("http://{0}:5984/_members".format(name),  auth=creds)
                else:
                    remote_resp = requests.get("http://{0}:5984/_members".format(name))
            print("CouchDB cluster peer {} ready to form a cluster".format(name))
        # At this point ALL peers have _nodes populated. Finish the cluster setup!
        if creds[0] and creds[1]:
            setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={"action": "finish_cluster"},  auth=creds)
        else:
            setup_resp=requests.post("http://127.0.0.1:5984/_cluster_setup", json={"action": "finish_cluster"})
        if (setup_resp.status_code == requests.codes.ok):
            print("CouchDB cluster setup done. Time to relax!")
        else:
            print('Ouch! Failed the final step: http://127.0.0.1:5984/_cluster_setup returned {0}'.format(setup_resp.status_code))
    else:
        print("This pod is intentionally skipping the call to http://127.0.0.1:5984/_cluster_setup")


def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    peer_names = discover_peers(construct_service_record())
    connect_the_dots(peer_names)
    print('Cluster membership populated!')
    finish_cluster(peer_names)
    sleep_forever()
