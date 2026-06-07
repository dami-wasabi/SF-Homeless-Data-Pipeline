// infra/lib/e84-pilot-stack.ts
// ─────────────────────────────────────────────────────────────────────────────
// AWS CDK stack for the E84 SF Homeless Pilot – Architecture A (Serverless).
//
// Resources created
// ─────────────────
//  S3
//    • data-bucket          – internal raw CSV storage (versioned)
//
//  Lambda
//    • etl-function         – ETL pipeline (S3 trigger + EventBridge scheduler)
//    • api-function         – REST API handler (API Gateway integration)
//
//  DynamoDB
//    • encounters-table     – merged encounter records
//      GSI: shelter-date-index (PK: shelter, SK: encounter_date)
//
//  API Gateway (HTTP API v2 – cheaper than REST API)
//    • pilot-api            – routes to api-function
//
//  EventBridge Scheduler
//    • partner-sync-rule    – nightly cron → etl-function (partner S3 poll)
//
//  SSM Parameter Store
//    • /e84-pilot/last-processed/*  – persisted across Lambda invocations
//
//  CloudWatch
//    • ETL error alarm      – Lambda error rate > 1 in 5 min → SNS → email
//    • API error alarm      – 5xx rate alarm
//    • Dashboard            – CloudWatch dashboard with key metrics
//
//  SNS
//    • alerts-topic         – receives alarm notifications
//
//  CloudFront + S3
//    • frontend-bucket      – React build artefacts
//    • distribution         – CDN for the dashboard UI
//
// Usage
// ─────
//   cd infra
//   npm install
//   npx cdk bootstrap          # first time only, once per account/region
//   npx cdk deploy             # deploys the stack
//   npx cdk diff               # preview changes before deploy
//
// ─────────────────────────────────────────────────────────────────────────────

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwv2integrations from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subscriptions from "aws-cdk-lib/aws-sns-subscriptions";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as path from "path";

export interface E84PilotStackProps extends cdk.StackProps {
  /** Email address that receives CloudWatch alarm notifications. */
  alertEmail: string;
  /** Name of the partner's public S3 bucket to poll for updates. */
  partnerBucket: string;
  /** S3 key of the partner dataset CSV inside partnerBucket. */
  partnerKey: string;
}

export class E84PilotStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: E84PilotStackProps) {
    super(scope, id, props);

    // ─── 1. Internal data bucket ─────────────────────────────────────────────
    const dataBucket = new s3.Bucket(this, "DataBucket", {
      bucketName: `e84-pilot-data-${this.account}-${this.region}`,
      versioned: true,                        // keep history of CSV updates
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy: cdk.RemovalPolicy.RETAIN, // never auto-delete data
    });

    // ─── 2. DynamoDB table ───────────────────────────────────────────────────
    const encountersTable = new dynamodb.Table(this, "EncountersTable", {
      tableName: "e84-pilot-encounters",
      partitionKey: { name: "hid",            type: dynamodb.AttributeType.STRING },
      sortKey:      { name: "encounter_date", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,  // on-demand = no idle cost
      pointInTimeRecovery: true,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // GSI: query all encounters at a given shelter within a date range
    encountersTable.addGlobalSecondaryIndex({
      indexName:     "shelter-date-index",
      partitionKey:  { name: "shelter",        type: dynamodb.AttributeType.STRING },
      sortKey:       { name: "encounter_date", type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    // ─── 3. Lambda shared layer path ─────────────────────────────────────────
    // The ETL source lives one directory up from infra/
    const lambdaCodePath = path.join(__dirname, "../../");

    // ─── 4. ETL Lambda ───────────────────────────────────────────────────────
    const etlFunction = new lambda.Function(this, "EtlFunction", {
      functionName: "e84-pilot-etl",
      runtime:      lambda.Runtime.PYTHON_3_12,
      handler:      "lambda.etl_handler.handler",
      code:         lambda.Code.fromAsset(lambdaCodePath, {
        // Exclude infra/ and dashboard/ from the deployment package
        exclude: ["infra/**", "dashboard/**", "*.md", ".git/**", "node_modules/**"],
      }),
      timeout:     cdk.Duration.minutes(5),
      memorySize:  256,
      environment: {
        INTERNAL_BUCKET:  dataBucket.bucketName,
        DEMOGRAPHICS_KEY: "raw/SF_HOMELESS_DEMOGRAPHICS.csv",
        ANXIETY_KEY:      "raw/SF_HOMELESS_ANXIETY.csv",
        PARTNER_BUCKET:   props.partnerBucket,
        PARTNER_KEY:      props.partnerKey,
        DYNAMODB_TABLE:   encountersTable.tableName,
        LOG_LEVEL:        "INFO",
      },
      logRetention: logs.RetentionDays.ONE_MONTH,
      tracing:      lambda.Tracing.ACTIVE,         // X-Ray at ~zero cost on pilot traffic
    });

    // Grant permissions
    dataBucket.grantRead(etlFunction);
    encountersTable.grantWriteData(etlFunction);

    // Read from the partner's public bucket (no credentials needed for public
    // buckets, but the Lambda execution role needs s3:GetObject on *)
    etlFunction.addToRolePolicy(new iam.PolicyStatement({
      actions:   ["s3:GetObject", "s3:HeadObject", "s3:ListBucket"],
      resources: [
        `arn:aws:s3:::${props.partnerBucket}`,
        `arn:aws:s3:::${props.partnerBucket}/*`,
      ],
    }));

    // SSM: read + write last-processed timestamps
    etlFunction.addToRolePolicy(new iam.PolicyStatement({
      actions:   ["ssm:GetParameter", "ssm:PutParameter"],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/e84-pilot/*`],
    }));

    // ─── 5. S3 event trigger → ETL Lambda ────────────────────────────────────
    // Any object PUT into the raw/ prefix fires the ETL pipeline
    dataBucket.addEventNotification(
      s3.EventType.OBJECT_CREATED,
      new s3n.LambdaDestination(etlFunction),
      { prefix: "raw/" },
    );

    // ─── 6. EventBridge Scheduler – nightly partner S3 poll ──────────────────
    const partnerSyncRule = new events.Rule(this, "PartnerSyncRule", {
      ruleName:    "e84-pilot-partner-sync",
      description: "Nightly check of partner public S3 bucket for updated datasets",
      schedule:    events.Schedule.cron({ minute: "0", hour: "2" }), // 02:00 UTC
    });
    partnerSyncRule.addTarget(new targets.LambdaFunction(etlFunction));

    // ─── 7. API Lambda ───────────────────────────────────────────────────────
    const apiFunction = new lambda.Function(this, "ApiFunction", {
      functionName: "e84-pilot-api",
      runtime:      lambda.Runtime.PYTHON_3_12,
      handler:      "lambda.api_handler.handler",
      code:         lambda.Code.fromAsset(lambdaCodePath, {
        exclude: ["infra/**", "dashboard/**", "*.md", ".git/**", "node_modules/**"],
      }),
      timeout:     cdk.Duration.seconds(30),
      memorySize:  256,
      environment: {
        DYNAMODB_TABLE:  encountersTable.tableName,
        ALLOWED_ORIGIN:  "*",                   // tighten to CloudFront domain post-pilot
        LOG_LEVEL:       "INFO",
      },
      logRetention: logs.RetentionDays.ONE_MONTH,
      tracing:      lambda.Tracing.ACTIVE,
    });

    encountersTable.grantReadData(apiFunction);

    // ─── 8. API Gateway HTTP API ──────────────────────────────────────────────
    const httpApi = new apigwv2.HttpApi(this, "PilotApi", {
      apiName:     "e84-pilot-api",
      description: "SF Homeless Pilot – dashboard API",
      corsPreflight: {
        allowOrigins: ["*"],
        allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.OPTIONS],
        allowHeaders: ["Content-Type"],
      },
    });

    const apiFnIntegration = new apigwv2integrations.HttpLambdaIntegration(
      "ApiIntegration",
      apiFunction,
    );

    // Mount all GET routes to the same Lambda; routing is handled in Python
    const routes = [
      "/summary",
      "/shelters",
      "/shelters/{shelter}/encounters",
      "/encounters",
      "/encounters/{hid}",
    ];
    routes.forEach((routePath) => {
      httpApi.addRoutes({
        path:        routePath,
        methods:     [apigwv2.HttpMethod.GET],
        integration: apiFnIntegration,
      });
    });

    // ─── 9. Frontend: S3 + CloudFront ────────────────────────────────────────
    const frontendBucket = new s3.Bucket(this, "FrontendBucket", {
      bucketName:         `e84-pilot-frontend-${this.account}-${this.region}`,
      blockPublicAccess:  s3.BlockPublicAccess.BLOCK_ALL,
      removalPolicy:      cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects:  true,
    });

    const distribution = new cloudfront.Distribution(this, "FrontendDistribution", {
      comment:       "E84 Pilot – React Dashboard",
      defaultBehavior: {
        origin:               new origins.S3Origin(frontendBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy:          cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      defaultRootObject: "index.html",
      errorResponses: [
        // SPA fallback: all 404s serve index.html so React Router handles routing
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: "/index.html" },
      ],
    });

    // ─── 10. SNS alerts topic ────────────────────────────────────────────────
    const alertsTopic = new sns.Topic(this, "AlertsTopic", {
      topicName:   "e84-pilot-alerts",
      displayName: "E84 Pilot Alerts",
    });
    alertsTopic.addSubscription(
      new subscriptions.EmailSubscription(props.alertEmail)
    );

    // ─── 11. CloudWatch alarms ───────────────────────────────────────────────
    // ETL errors
    const etlErrorAlarm = new cloudwatch.Alarm(this, "EtlErrorAlarm", {
      alarmName:          "e84-pilot-etl-errors",
      alarmDescription:   "ETL Lambda threw an error",
      metric:             etlFunction.metricErrors({
        period:    cdk.Duration.minutes(5),
        statistic: "Sum",
      }),
      threshold:          1,
      evaluationPeriods:  1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData:   cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    etlErrorAlarm.addAlarmAction(new actions.SnsAction(alertsTopic));

    // API 5xx errors
    const apiErrorAlarm = new cloudwatch.Alarm(this, "ApiErrorAlarm", {
      alarmName:          "e84-pilot-api-5xx",
      alarmDescription:   "API Lambda 5xx error rate elevated",
      metric:             apiFunction.metricErrors({
        period:    cdk.Duration.minutes(5),
        statistic: "Sum",
      }),
      threshold:          5,
      evaluationPeriods:  1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData:   cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    apiErrorAlarm.addAlarmAction(new actions.SnsAction(alertsTopic));

    // ─── 12. CloudWatch operational dashboard ───────────────────────────────
    const cwDashboard = new cloudwatch.Dashboard(this, "OperationalDashboard", {
      dashboardName: "e84-pilot-operations",
    });
    cwDashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title:  "ETL Lambda – invocations & errors",
        left:   [etlFunction.metricInvocations(), etlFunction.metricErrors()],
        width:  12,
      }),
      new cloudwatch.GraphWidget({
        title:  "API Lambda – invocations & errors",
        left:   [apiFunction.metricInvocations(), apiFunction.metricErrors()],
        width:  12,
      }),
      new cloudwatch.GraphWidget({
        title:  "ETL Lambda – duration (ms)",
        left:   [etlFunction.metricDuration({ statistic: "p90" })],
        width:  12,
      }),
      new cloudwatch.GraphWidget({
        title:  "DynamoDB – consumed write capacity",
        left:   [encountersTable.metricConsumedWriteCapacityUnits()],
        width:  12,
      }),
    );

    // ─── 13. Outputs ─────────────────────────────────────────────────────────
    new cdk.CfnOutput(this, "ApiEndpoint", {
      description: "HTTP API base URL – set as REACT_APP_API_URL in the frontend build",
      value:       httpApi.apiEndpoint,
    });
    new cdk.CfnOutput(this, "CloudFrontUrl", {
      description: "Dashboard URL",
      value:       `https://${distribution.distributionDomainName}`,
    });
    new cdk.CfnOutput(this, "DataBucketName", {
      description: "Upload raw CSVs here to trigger the ETL pipeline",
      value:       dataBucket.bucketName,
    });
    new cdk.CfnOutput(this, "DynamoTableName", {
      description: "DynamoDB table holding merged encounter records",
      value:       encountersTable.tableName,
    });
    new cdk.CfnOutput(this, "FrontendBucketName", {
      description: "Deploy the React build here: aws s3 sync build/ s3://<this>",
      value:       frontendBucket.bucketName,
    });
  }
}
