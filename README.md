# CouchDB StatefulSet Assembler

This code is intended to be deployed as a sidecar container in a Pod alongside
the official CouchDB container when running CouchDB as a Kubernetes StatefulSet.
It will resolve the DNS records maintained by Kubernetes to discover the other
peers in the cluster, then automatically populate the Pod's _nodes DB with the
names of the other nodes. The end result is that the cluster is automically
joined up.

If the deployment is scaled *up* after the initial creation those new Pods will
be automatically added. Scaling *down* does not automatically remove the Pods
from the membership database at this time.
