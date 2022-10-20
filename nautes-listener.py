import sys
import argparse
import json
import base64
import asyncio
import nats
import stan
import os
import time
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError
from nats.aio.client import Client as NATS
from stan.aio.client import Client as STAN
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import urllib3
urllib3.disable_warnings()

parser = argparse.ArgumentParser()
parser.add_argument('-p', '--project', help='argocd project name', required=True)
parser.add_argument('-a', '--application', help='argocd application name', required=True)
parser.add_argument('-c', '--commit', help='commit SHA', required=True)
parser.add_argument('-t', '--timeout', help='maximum time to wait in seconds', required=True)
parser.add_argument('-n', '--namespace', help='namespace containing eventbus', required=True)
parser.add_argument('-e', '--eventbus', help='eventbus name', required=False)
parser.add_argument('-k', '--kubeconfig', help='the path to kubeconfig', required=False)
args = parser.parse_args()

eventbus = args.eventbus
if not eventbus:
    eventbus = "default"
ns = args.namespace
timeout = int(args.timeout)
project_name = args.project
app_name = args.application
commit = args.commit
step = 2
exit_code = -1

async def main():
    nc = NATS()
    options = get_nats_connection_options()
    await nc.connect(**options)

    sc = STAN()
    cluster_id = "eventbus-" + eventbus
    client_id = "client-" + str(int(time.time()))
    print("connect to {} by {}".format(cluster_id, client_id))
    await sc.connect(cluster_id, client_id, nats=nc)

    async def message_handler(msg):
        eventJson = msg.data.decode('utf-8')
        event = json.loads(eventJson)
        if event['type'] != "webhook" or event['subject'] != "deployments-status":
            print("received a invalid message, type: {}, subject: {}".format(event['type'], event['subject']))
            return

        dataJson = base64.b64decode(event['data_base64'])
        data = json.loads(dataJson)
        body = data['body']

        if body['project'] == project_name and body['application'] == app_name and body['revision'] == commit:
            print("received a valid message, data:")
            print(data)
            global exit_code
            phase = body['phase']
            sync_status = body['sync_status']
            healthy = body['healthy']
            if phase == "Succeeded" and healthy == "Healthy":
                exit_code = 0
            elif phase == "Failed" or phase == "Error":
                exit_code = 1
            else: 
                print("deployment in progressing..., phase:{}, sync_status:{}, healthy:{}".format(phase, sync_status, healthy))
        else:
            print("received a invalid message, project: {}, application: {}, revision: {}".format(body['project'], body['application'], body['revision']))

    async def error_handler(error):
        print("error: " + error)
        global exit_code
        exit_code = 1

    subject = "eventbus-" + ns
    sub = await sc.subscribe(subject = subject, cb = message_handler, start_at = 0, error_cb = error_handler)
    print("Listening for '{}/{}' ...".format(project_name, app_name))

    count = int(timeout/step)
    for i in range(1, count):
        await asyncio.sleep(step)
        if exit_code >= 0:
            await exit(sub, nc, exit_code)
    print("timeout and exit: " + str(timeout) + "s")
    await exit(sub, nc, 1)

async def exit(sub, nc, exit_code):
    await sub.unsubscribe()
    await nc.drain()
    sys.exit(exit_code)

def get_nats_connection_options():
    if args.kubeconfig:
        config.load_kube_config(config_file=args.kubeconfig)
    else:
        config.load_incluster_config()
    v1api = client.CoreV1Api()
    custom_object_api = client.CustomObjectsApi()

    eventbus_pods = v1api.list_namespaced_pod(namespace=ns, label_selector="eventbus-name=" + eventbus)
    servers = []
    for eventbus_pod in eventbus_pods.items:
        pod_ip = eventbus_pod.status.pod_ip
        servers.append("nats://{}:4222".format(pod_ip))

    secret_name = "eventbus-" + eventbus + "-client"
    eventbus_client_secret = v1api.read_namespaced_secret(name=secret_name, namespace=ns)
    token_kv_base64 = eventbus_client_secret.data['client-auth']
    token_kv = base64.b64decode(token_kv_base64)
    token = token_kv.split(b':')[1].replace(b"\"", b"").decode('utf-8').strip()
    options = {"servers": servers, "token": token}
    return options


if __name__ == '__main__':
    asyncio.run(main())