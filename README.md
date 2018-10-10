# CouchDB StatefulSet Assembler

This code is intended to be deployed as a sidecar container in a Pod alongside
the [semi-official CouchDB Docker
image](https://hub.docker.com/r/apache/couchdb/) when running CouchDB as a
Kubernetes StatefulSet. It will resolve the DNS records maintained by Kubernetes
to discover the other peers in the cluster, then automatically populate the
Pod's _nodes DB with the names of the other nodes. The end result is that the
cluster is automically joined up.

If the deployment is scaled *up* after the initial creation those new Pods will
be automatically added. Scaling *down* does not automatically remove the Pods
from the membership database at this time.

# Added functionality on top of upstream repo (kocolosk/couchdb-statefulset-assembler)
1. Ensure that all CouchDB containers get all peers registered in _node db_
2. Do the POST http://127.0.0.1:5984/_cluster_setup , payload {"action": "finish_cluster"} on pod with ordinal nr 0 (if username and password provided)

The intention is that the cluster will succeed with setup even if some of the CouchDB nodes are restarted during cluster formation (e.g. due to Liveness or Readiness settings).

Below are example logs from formation of a 3-node cluster.
```
kubectl logs -f my-release-couchdb-2 -c couchdb-statefulset-assembler       
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
	| Got the following peers' fqdm from DNS lookup: ['my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local']
Adding CouchDB cluster node my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Probing my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Cluster membership populated!
Intentionally skipping the POST to http://127.0.0.1:5984/_cluster_setup {"action": "finish_cluster"}
```

```
kubectl logs -f my-release-couchdb-1 -c couchdb-statefulset-assembler
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
	| Got the following peers' fqdm from DNS lookup: ['my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local']
Adding CouchDB cluster node my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Probing my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Cluster membership populated!
Intentionally skipping the POST to http://127.0.0.1:5984/_cluster_setup {"action": "finish_cluster"}
```

```
kubectl logs -f my-release-couchdb-0 -c couchdb-statefulset-assembler
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
	| Got the following peers' fqdm from DNS lookup: ['my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local']
Adding CouchDB cluster node my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local
	| Response: 201 {'ok': True, 'id': 'couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'rev': '1-967a00dff5e02add41819138abb3284d'}
Adding CouchDB cluster node my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local
	| Response: 201 {'ok': True, 'id': 'couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'rev': '1-967a00dff5e02add41819138abb3284d'}
Adding CouchDB cluster node my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Probing my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local for cluster membership
	| Cluster members in my-release-couchdb-0 not yet present in my-release-couchdb-2: {'couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local'}
Probing my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local for cluster membership
	| Cluster members in my-release-couchdb-0 not yet present in my-release-couchdb-1: {'couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local'}
Probing my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Resolving SRV record _couchdb._tcp.my-release-couchdb.default.svc.cluster.local
	| Got the following peers' fqdm from DNS lookup: ['my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local', 'my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local']
Adding CouchDB cluster node my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Adding CouchDB cluster node my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local to this pod's CouchDB.
	| Request: PUT http://127.0.0.1:5986/_nodes/couchdb@my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local
	| Response: 409 {'error': 'conflict', 'reason': 'Document update conflict.'}
Probing my-release-couchdb-2.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-1.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Probing my-release-couchdb-0.my-release-couchdb.default.svc.cluster.local for cluster membership
	| In sync!
Cluster membership populated!
Get the cluster up and running
	| Request: POST http://127.0.0.1:5984/_cluster_setup , payload {"action": "finish_cluster"}
	|	Response: 201 {'ok': True}
	| Sweet! Just a final check for the logs...
	| Request: GET http://127.0.0.1:5984/_cluster_setup
	|	Response: 200 {'state': 'cluster_finished'}
Time to relax!
```
