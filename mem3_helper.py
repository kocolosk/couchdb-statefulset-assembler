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

def join_node(name):
    creds = (os.getenv("COUCHDB_USER"), os.getenv("COUCHDB_PASSWORD"))
    uri = "http://127.0.0.1:5986/_nodes/couchdb@{0}".format(name)
    doc = {}
    if creds[0] and creds[1]:
        resp = requests.put(uri, data=json.dumps(doc), auth=creds)
    else:
        resp = requests.put(uri, data=json.dumps(doc))
    return resp

@backoff.on_exception(
    backoff.expo,
    requests.exceptions.ConnectionError,
    max_tries=10
)
def connect_the_dots(names):
    for name in names:
        resp = join_node(name)
        while resp.status_code == 404:
            print('Waiting for _nodes DB to be created ...')
            time.sleep(5)
            resp = join_node(name)
        print('Adding cluster member', name, resp.status_code)

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    connect_the_dots(discover_peers(construct_service_record()))
    print('Cluster membership populated!')
    sleep_forever()
