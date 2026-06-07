#!/usr/bin/env node
// infra/bin/app.ts
// CDK app entrypoint – instantiates the pilot stack.
//
// Override defaults via environment variables:
//   ALERT_EMAIL     email to receive CloudWatch alarms   (required in prod)
//   PARTNER_BUCKET  name of the partner public S3 bucket
//   PARTNER_KEY     S3 key of the partner CSV file

import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { E84PilotStack } from "../lib/e84-pilot-stack";

const app = new cdk.App();

new E84PilotStack(app, "E84PilotStack", {
  alertEmail:    process.env.ALERT_EMAIL    ?? "devops@element84.com",
  partnerBucket: process.env.PARTNER_BUCKET ?? "sf-open-data-public",
  partnerKey:    process.env.PARTNER_KEY    ?? "homeless/partner_dataset.csv",
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region:  process.env.CDK_DEFAULT_REGION ?? "us-east-1",
  },
  tags: {
    Project:     "e84-homeless-pilot",
    Environment: process.env.APP_ENV ?? "dev",
    ManagedBy:   "cdk",
  },
});
