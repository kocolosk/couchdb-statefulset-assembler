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

def discover_peers_dns(retries=5):
    # Drop our Pod's unique identity and replace with '_couchdb._tcp'
    # to form the name of the SRV record
    resource = '.'.join(['_couchdb', '_tcp'] + socket.getfqdn().split('.')[1:])
    print ('SRV record', resource)
    if retries > 0:
        try:
            answers = dns.resolver.query(resource, 'SRV')
        except dns.resolver.NXDOMAIN:
            print('DNS query for SRV records failed with NXDOMAIN error')
            time.sleep(5)
            discover_peers_dns(retries - 1)
        # Erlang requires that we drop the trailing period from the absolute DNS
        # name to form the hostname used for the Erlang node. This feels hacky
        # but not sure of a more official answer
        hostnames = [rdata.target.to_text()[:-1] for rdata in answers]
        print(hostnames)
        connect_the_dots(hostnames)
    else:
        print('Could not resolve resource ', resource)

def connect_the_dots(names, retries=5):
    if retries > 0:
        for name in names:
            uri = "http://127.0.0.1:5986/_nodes/couchdb@{0}".format(name)
            doc = {}
            try:
                resp = requests.put(uri, data=json.dumps(doc))
                if resp.status_code == 404:
                    print('CouchDB nodes DB does not exist yet')
                    time.sleep(5)
                    connect_the_dots(names, retries - 1)
                print(name, resp.status_code)
            except requests.exceptions.ConnectionError:
                print('CouchDB admin port not up yet')
                time.sleep(5)
                connect_the_dots(names, retries - 1)
        sleep_forever()
    else:
        print('Could not connect to local admin port to supply node names')

def sleep_forever():
    while True:
        time.sleep(5)

if __name__ == '__main__':
    discover_peers_dns()
