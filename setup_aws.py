#!/usr/bin/env python3
"""Provision Suno Studio's optional pay-per-use AWS renderer.

Run after ``pip install -r requirements-cloud.txt`` and ``aws configure`` (or
AWS SSO login). This script writes resource IDs to ~/.suno_studio/config.json
without storing AWS access keys. It is safe to rerun after a partial setup.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import secrets
import sys
import time
import zipfile
from getpass import getpass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path.home() / ".suno_studio" / "config.json"
PREFIX = "suno-studio"


def _aws(profile, region):
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("install AWS support: pip install -r requirements-cloud.txt") from error
    return boto3.Session(profile_name=profile or None, region_name=region)


def _missing(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code") in (
        "NoSuchEntity", "ResourceNotFoundException", "RepositoryNotFoundException",
        "ClusterNotFoundException", "404", "NotFoundException")


def _worker_access_policies(bucket_arn, secret_arn=""):
    render = [
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": f"{bucket_arn}/render-inputs/*"},
        {"Effect": "Allow", "Action": "s3:PutObject",
         "Resource": f"{bucket_arn}/render-results/*"},
    ]
    delivery = [
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": f"{bucket_arn}/delivery-inputs/*"},
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": f"{bucket_arn}/render-results/*"},
        {"Effect": "Allow", "Action": "s3:PutObject",
         "Resource": f"{bucket_arn}/delivery-results/*"},
    ]
    if secret_arn:
        delivery.append({"Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
                         "Resource": secret_arn})
    return render, delivery


def _role(iam, name, service, statements, managed=()):
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": service}, "Action": "sts:AssumeRole"}]}
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except Exception as error:
        if not _missing(error):
            raise
        arn = iam.create_role(RoleName=name,
                              AssumeRolePolicyDocument=json.dumps(trust),
                              Description="Suno Studio optional cloud work")["Role"]["Arn"]
    else:
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    for policy_arn in managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
    if statements:
        iam.put_role_policy(RoleName=name, PolicyName="suno-studio-access",
                            PolicyDocument=json.dumps({"Version": "2012-10-17",
                                                       "Statement": statements}))
    return arn


def _bucket(session, account, region):
    s3 = session.client("s3")
    name = f"suno-studio-{account}-{region}"
    try:
        s3.head_bucket(Bucket=name)
    except Exception as error:
        if getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
        kwargs = {"Bucket": name}
        if region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**kwargs)
    s3.put_public_access_block(Bucket=name, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True,
        "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=name, ServerSideEncryptionConfiguration={"Rules": [{
        "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=name, LifecycleConfiguration={"Rules": [
        {"ID": "render-inputs", "Status": "Enabled", "Filter": {"Prefix": "render-inputs/"},
         "Expiration": {"Days": 7}},
        {"ID": "delivery-inputs", "Status": "Enabled", "Filter": {"Prefix": "delivery-inputs/"},
         "Expiration": {"Days": 7}},
        {"ID": "delivery-objects", "Status": "Enabled", "Filter": {"Prefix": "delivery-objects/"},
         "Expiration": {"Days": 4}},
        {"ID": "render-results", "Status": "Enabled", "Filter": {"Prefix": "render-results/"},
         "Expiration": {"Days": 30}},
        {"ID": "delivery-results", "Status": "Enabled", "Filter": {"Prefix": "delivery-results/"},
         "Expiration": {"Days": 30}},
        {"ID": "build-inputs", "Status": "Enabled", "Filter": {"Prefix": "build-inputs/"},
         "Expiration": {"Days": 7}},
    ]})
    return name


def _network(session, create=True):
    ec2 = session.client("ec2")
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        raise RuntimeError("no default VPC exists; create one or provide subnets before setup")
    vpc_id = vpcs[0]["VpcId"]
    subnets = [item["SubnetId"] for item in ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]]
    if not subnets:
        raise RuntimeError("default VPC has no subnets")
    groups = ec2.describe_security_groups(Filters=[
        {"Name": "vpc-id", "Values": [vpc_id]},
        {"Name": "group-name", "Values": ["suno-studio-egress"]}])["SecurityGroups"]
    group_id = groups[0]["GroupId"] if groups else (
        ec2.create_security_group(
            GroupName="suno-studio-egress", Description="Outbound-only Suno Studio tasks",
            VpcId=vpc_id)["GroupId"] if create else "")
    return vpc_id, subnets[:3], group_id


def _secret(session, name, no_slack):
    client = session.client("secretsmanager")
    try:
        existing = client.describe_secret(SecretId=name)
    except Exception as error:
        if not _missing(error):
            raise
        existing = None
    if no_slack:
        return existing["ARN"] if existing else ""
    if existing:
        print("Slack token already stored in Secrets Manager; press Enter to keep it.")
    token = getpass("Slack bot token (leave blank to keep/skip): ").strip()
    if token:
        if not token.startswith("xoxb-"):
            raise ValueError("expected a Slack bot token beginning xoxb-")
        if existing:
            client.put_secret_value(SecretId=name, SecretString=token)
        else:
            existing = client.create_secret(Name=name, SecretString=token,
                                            Description="Suno Studio Slack bot token")
    return existing["ARN"] if existing else ""


def _ecr(session, account, region):
    ecr = session.client("ecr")
    try:
        ecr.describe_repositories(repositoryNames=[PREFIX])
    except Exception as error:
        if not _missing(error):
            raise
        ecr.create_repository(repositoryName=PREFIX,
                              imageScanningConfiguration={"scanOnPush": True})
    return f"{account}.dkr.ecr.{region}.amazonaws.com/{PREFIX}"


def _source_zip():
    files = ("Dockerfile.aws", "requirements-cloud.txt", "aws_worker.py",
             "suno_studio.py")
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(ROOT / name, name)
    return memory.getvalue()


def _build_image(session, account, region, bucket, repo, role_arn):
    source = _source_zip()
    tag = hashlib.sha256(source).hexdigest()[:16]
    image = f"{repo}:{tag}"
    ecr = session.client("ecr")
    try:
        ecr.describe_images(repositoryName=PREFIX, imageIds=[{"imageTag": tag}])
        print(f"Reusing image {image}")
        return image
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "ImageNotFoundException":
            raise
    key = f"build-inputs/source-{tag}.zip"
    session.client("s3").put_object(Bucket=bucket, Key=key, Body=source,
                                    ContentType="application/zip")
    buildspec = """version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
  build:
    commands:
      - docker build -f Dockerfile.aws -t $BUILD_IMAGE .
  post_build:
    commands:
      - docker push $BUILD_IMAGE
"""
    project = "suno-studio-build"
    source_config = {"type": "S3", "location": f"{bucket}/{key}",
                     "buildspec": buildspec}
    environment = {"type": "LINUX_CONTAINER", "image": "aws/codebuild/standard:7.0",
                   "computeType": "BUILD_GENERAL1_MEDIUM", "privilegedMode": True,
                   "environmentVariables": [
                       {"name": "BUILD_IMAGE", "value": image},
                       {"name": "ECR_REGISTRY", "value": repo.split("/", 1)[0]},
                   ]}
    codebuild = session.client("codebuild")
    config = {"source": source_config, "artifacts": {"type": "NO_ARTIFACTS"},
              "environment": environment, "serviceRole": role_arn,
              "timeoutInMinutes": 30,
              "logsConfig": {"cloudWatchLogs": {"status": "ENABLED",
                                              "groupName": "/aws/codebuild/suno-studio"}}}
    existing = codebuild.batch_get_projects(names=[project])["projects"]
    if existing:
        codebuild.update_project(name=project, **config)
    else:
        codebuild.create_project(name=project, **config)
    started = codebuild.start_build(projectName=project)["build"]["id"]
    print(f"Building the worker image remotely with CodeBuild ({started})…")
    while True:
        status = codebuild.batch_get_builds(ids=[started])["builds"][0]["buildStatus"]
        if status == "SUCCEEDED":
            return image
        if status in ("FAILED", "FAULT", "STOPPED", "TIMED_OUT"):
            raise RuntimeError(f"CodeBuild finished with {status}; inspect build {started}")
        time.sleep(15)


def _cluster_and_tasks(session, image, execution_role, render_role, delivery_role,
                       secret_arn, region, bucket):
    ecs = session.client("ecs")
    cluster = "suno-studio"
    clusters = ecs.describe_clusters(clusters=[cluster])["clusters"]
    if not clusters or clusters[0]["status"] != "ACTIVE":
        ecs.create_cluster(clusterName=cluster)
    logs = session.client("logs")
    try:
        logs.create_log_group(logGroupName="/ecs/suno-studio")
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "ResourceAlreadyExistsException":
            raise

    def task(family, cpu, memory, role, env):
        definition = ecs.register_task_definition(
            family=family, requiresCompatibilities=["FARGATE"], networkMode="awsvpc",
            cpu=str(cpu), memory=str(memory), executionRoleArn=execution_role,
            taskRoleArn=role,
            containerDefinitions=[{"name": "suno-worker", "image": image,
                "essential": True, "environment": [
                    {"name": key, "value": value} for key, value in env.items()],
                "logConfiguration": {"logDriver": "awslogs", "options": {
                    "awslogs-group": "/ecs/suno-studio", "awslogs-region": region,
                    "awslogs-stream-prefix": family}}}])
        return definition["taskDefinition"]["taskDefinitionArn"]

    render_task = task("suno-studio-render", 4096, 8192, render_role,
                       {"SUNO_BUCKET": bucket})
    delivery_task = task("suno-studio-delivery", 1024, 2048, delivery_role,
                         {"SUNO_BUCKET": bucket,
                          **({"SLACK_SECRET_ARN": secret_arn} if secret_arn else {})})
    return cluster, render_task, delivery_task


def _link_function(session, role_arn, bucket, secret, region):
    client = session.client("lambda")
    name = "suno-studio-private-link"
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "aws_link.py", "aws_link.py")
    code = memory.getvalue()
    env = {"Variables": {"SUNO_BUCKET": bucket, "LINK_SECRET": secret}}
    try:
        client.get_function(FunctionName=name)
        exists = True
    except Exception as error:
        if not _missing(error):
            raise
        exists = False
    if exists:
        client.update_function_code(FunctionName=name, ZipFile=code)
        client.get_waiter("function_updated").wait(FunctionName=name)
        client.update_function_configuration(FunctionName=name, Runtime="python3.11",
                                             Handler="aws_link.handler", Environment=env,
                                             Role=role_arn, Timeout=15, MemorySize=128)
    else:
        client.create_function(FunctionName=name, Runtime="python3.11",
                               Role=role_arn, Handler="aws_link.handler",
                               Code={"ZipFile": code}, Environment=env,
                               Timeout=15, MemorySize=128,
                               Description="Short S3 redirects for Suno Studio email links")
    client.get_waiter("function_active").wait(FunctionName=name)
    try:
        url_config = client.get_function_url_config(FunctionName=name)
    except Exception as error:
        if not _missing(error):
            raise
        url = client.create_function_url_config(
            FunctionName=name, AuthType="NONE")["FunctionUrl"]
    else:
        url = url_config["FunctionUrl"]
        if url_config.get("AuthType") != "NONE":
            client.update_function_url_config(FunctionName=name, AuthType="NONE")
    for statement, action, extra in (
        ("suno-public-url", "lambda:InvokeFunctionUrl", {"FunctionUrlAuthType": "NONE"}),
        ("suno-public-invoke-via-url", "lambda:InvokeFunction",
         {"InvokedViaFunctionUrl": True}),
    ):
        try:
            client.add_permission(FunctionName=name, StatementId=statement,
                                  Action=action, Principal="*", **extra)
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") != "ResourceConflictException":
                raise
    return url


def _quota(session):
    try:
        result = session.client("service-quotas").get_service_quota(
            ServiceCode="fargate", QuotaCode="L-3032A538")
        return result["Quota"]["Value"]
    except Exception:
        return None


def _read_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save_config(changes):
    config = _read_config()
    config.update(changes)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_name("config.json.aws-setup-tmp")
    try:
        temporary.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(CONFIG_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def provision(args):
    session = _aws(args.profile, args.region)
    sts = session.client("sts")
    account = sts.get_caller_identity()["Account"]
    region = session.region_name
    if args.account and args.account != account:
        raise RuntimeError(f"current AWS identity is account {account}, expected {args.account}")
    vpc, subnets, security_group = _network(session, create=not args.check)
    quota = _quota(session)
    print(f"AWS account {account}; region {region}; default VPC {vpc}; subnets {', '.join(subnets)}")
    print(f"Fargate On-Demand vCPU quota: {quota if quota is not None else 'unavailable'}")
    if quota is not None and quota < 100:
        print("Request a quota increase to at least 100 vCPU for 25 parallel 4-vCPU renders.")
    if args.check:
        return
    bucket = _bucket(session, account, region)
    repo = _ecr(session, account, region)
    iam = session.client("iam")
    bucket_arn = f"arn:aws:s3:::{bucket}"
    secret_arn = _secret(session, "suno-studio/slack-token", args.no_slack)

    execution_role = _role(iam, "suno-studio-ecs-execution", "ecs-tasks.amazonaws.com", [],
                           managed=("arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",))
    render_access, delivery_access = _worker_access_policies(bucket_arn, secret_arn)
    render_role = _role(iam, "suno-studio-render", "ecs-tasks.amazonaws.com", render_access)
    delivery_role = _role(iam, "suno-studio-delivery", "ecs-tasks.amazonaws.com",
                          delivery_access)
    link_role = _role(iam, "suno-studio-link", "lambda.amazonaws.com", [
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": f"{bucket_arn}/delivery-objects/*"}],
        managed=("arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",))
    build_role = _role(iam, "suno-studio-build", "codebuild.amazonaws.com", [
        {"Effect": "Allow", "Action": ["s3:GetObject"],
         "Resource": f"{bucket_arn}/build-inputs/*"},
        {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
         "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/codebuild/suno-studio:*"},
        {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability",
                                       "ecr:CompleteLayerUpload", "ecr:InitiateLayerUpload",
                                       "ecr:PutImage", "ecr:UploadLayerPart"],
         "Resource": f"arn:aws:ecr:{region}:{account}:repository/{PREFIX}"},
    ])
    # IAM role propagation can lag role creation by a few seconds.
    time.sleep(8)
    image = _build_image(session, account, region, bucket, repo, build_role)
    cluster, render_task, delivery_task = _cluster_and_tasks(
        session, image, execution_role, render_role, delivery_role, secret_arn, region, bucket)
    existing = _read_config()
    link_secret = existing.get("aws_link_secret") or secrets.token_hex(32)
    link_url = _link_function(session, link_role, bucket, link_secret, region)
    _save_config({
        "aws_region": region, "aws_bucket": bucket, "aws_cluster": cluster,
        "aws_render_task": render_task, "aws_delivery_task": delivery_task,
        "aws_subnets": subnets, "aws_security_group": security_group,
        "aws_link_url": link_url, "aws_link_secret": link_secret,
        "aws_account_id": account, "aws_profile": args.profile or "",
    })
    print("AWS setup complete. The app stays on local rendering until you select AWS in Settings.")
    print(f"Bucket: s3://{bucket}; cluster: {cluster}; image: {image}")
    print(f"Render task: {render_task}")
    print(f"Delivery task: {delivery_task}")
    print(f"Link endpoint: {link_url}")
    print(f"App configuration saved in {CONFIG_PATH} (no AWS access keys stored).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION") or
                        os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    parser.add_argument("--profile", help="named AWS CLI profile; default is the current one")
    parser.add_argument("--account", help="expected AWS account ID; abort if identity differs")
    parser.add_argument("--no-slack", action="store_true", help="skip Slack token setup")
    parser.add_argument("--check", action="store_true", help="read-only identity/network/quota check")
    args = parser.parse_args(argv)
    try:
        provision(args)
    except Exception as error:
        print(f"AWS setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
