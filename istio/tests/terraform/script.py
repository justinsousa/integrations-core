#!/usr/bin/python

# Write it as a python script for portability
import glob
import os
from subprocess import check_call

version = os.environ['ISTIO_VERSION']

check_call(["curl", "-o", "istio.tar.gz", "-L", "https://github.com/istio/istio/archive/{}.tar.gz".format(version)])
check_call(["tar", "xf", "istio.tar.gz"])

check_call(["kubectl", "create", "ns", "istio-system"])
check_call(["kubectl", "label", "namespace", "default", "istio-injection=enabled"])

for f in glob.glob("./istio-{}/install/kubernetes/helm/istio-init/files/crd*.yaml".format(version)):
    check_call(["kubectl", "apply", "-f", f])

check_call(
    ["kubectl", "wait", "jobs", "--all", "--for=condition=complete", "--namespace=istio-system", "--timeout=300s"]
)

check_call(["kubectl", "apply", "-f", "./istio-{}/install/kubernetes/istio-demo-auth.yaml".format(version)])
check_call(["kubectl", "wait", "pods", "--all", "--for=condition=Ready", "--timeout=300s"])

check_call(["kubectl", "apply", "-f", "./istio-{}/samples/bookinfo/platform/kube/bookinfo.yaml".format(version)])
check_call(["kubectl", "wait", "pods", "--all", "--for=condition=Ready", "--timeout=300s"])

check_call(["kubectl", "apply", "-f", "./istio-{}/samples/bookinfo/networking/bookinfo-gateway.yaml".format(version)])
check_call(["kubectl", "wait", "pods", "--all", "--for=condition=Ready", "--timeout=300s"])
