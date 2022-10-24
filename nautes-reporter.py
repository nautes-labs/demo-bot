import argparse
import datetime
import time
import os
import http.client
import urllib
import json
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pprint import pprint
from enum import Enum
import urllib3
urllib3.disable_warnings()

parser = argparse.ArgumentParser()
parser.add_argument('-o', '--owner', help='github owner name', required=True)
parser.add_argument('-r', '--repo', help='github repository name', required=True)
parser.add_argument('-b', '--branch', help='github branch', required=True)
parser.add_argument('-c', '--commit', help='commit sha', required=True)
parser.add_argument('-t', '--tokenfile', help='the path to the file containing github personal access token', required=True)
parser.add_argument('-tu', '--tektonurl', help='tekton dashboard url', required=True)
parser.add_argument('-k', '--kubeconfig', help='the path to kubeconfig', required=False)
args = parser.parse_args()

class ConditionReason(Enum):
  completed = "Completed"
  succeeded = "Succeeded"
  failed = "Failed"
  running = "Running"
  pending = "Pending"

class CommitStatus(Enum):
  success = "success"
  failure = "failure"
  error = "error"
  pending = "pending"

owner = args.owner
repo = args.repo
branch = args.branch.replace('refs/heads/', '')
commitSHA = args.commit
tokenFile = args.tokenfile
tektonUrl = args.tektonurl
ns = os.getenv('NAMESPACE')
SLEEP_TIME = 10

with open(tokenFile,'rt') as f:
    token = f.read().strip('\n')

# get latest pipelinerun
if args.kubeconfig:
    config.load_kube_config(config_file=args.kubeconfig)
else:
    config.load_incluster_config()
custom_object_api = client.CustomObjectsApi()

latestPr = {}
while True:
    pipelineruns = custom_object_api.list_namespaced_custom_object(
            group="tekton.dev", version="v1beta1", namespace=ns, 
            plural="pipelineruns", label_selector="branch=" + branch)

    pipelinerunsItems = pipelineruns['items']
    latestPr = pipelinerunsItems[len(pipelinerunsItems) - 1]
    pipelinerunName = latestPr['metadata']['name']
    print("the latest pipelinerun: " + pipelinerunName)
    if 'status' not in latestPr or 'taskRuns' not in latestPr['status']: 
      time.sleep(SLEEP_TIME)
      continue
    taskruns = latestPr['status']['taskRuns']
    
    taskrunList = []
    for k,taskrunInMap in taskruns.items():
      if 'startTime' not in taskrunInMap['status']:
        continue
      startTimeInMap = datetime.datetime.strptime(
          taskrunInMap['status']['startTime'], '%Y-%m-%dT%H:%M:%SZ')
      l = 0
      for taskrunInList in taskrunList:
        startTimeInList = datetime.datetime.strptime(
            taskrunInList['status']['startTime'], '%Y-%m-%dT%H:%M:%SZ')
        if startTimeInMap < startTimeInList:
          break
        l = l + 1
      taskrunList.insert(l, taskrunInMap)
    
    context = "continuous-integration/tekton"
    targetUrl = tektonUrl + "/#/namespaces/" + ns + "/pipelineruns/" + pipelinerunName
    description = ""
    state = CommitStatus.pending.value
    condition = latestPr['status']['conditions'][0]
    conditionReason = condition['reason']
    if (conditionReason == ConditionReason.completed.value
         or conditionReason == ConditionReason.succeeded.value):
      state = CommitStatus.success.value
    elif conditionReason == ConditionReason.failed.value:
      state = CommitStatus.failure.value

    index = 0
    for taskrun in taskrunList:
      pipelineTaskName = taskrun['pipelineTaskName']
      if 'conditions' not in taskrun['status']:
          continue
      condition = taskrun['status']['conditions'][0]
      conditionReason = condition['reason']
      description = description + pipelineTaskName
      if index < len(taskrunList) - 1:
        description = description + ", "
      index = index + 1

    headers = {'User-Agent': 'gaozheng-cn', 'Accept':'application/vnd.github+json', 'Authorization': 'Bearer ' + token}
    commitStatus = {'state': state, "target_url": targetUrl, "description": description, 'context': context}
    requrl = "/repos/{}/{}/statuses/{}".format(owner, repo, commitSHA)
    conn = http.client.HTTPSConnection("api.github.com")
    conn.request(method="POST",url=requrl, body=json.dumps(commitStatus), headers=headers)
    response = conn.getresponse()
    if 'completionTime' in latestPr['status']:
        break

    time.sleep(SLEEP_TIME)