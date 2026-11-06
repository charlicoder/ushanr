# Workforce history & analytics — events, daily job and AWS setup

Tracks every therapist's schedule edits and, once a day, stores the full day (shift, break,
leave, extra hours, completed services, worked / idle time) in the **ushanr** database.

## Data flow

```
ushauth  (edit hours / leave / extra / therapist)
   └─ signal → outbox_events row (same DB transaction)
        └─ `publish_workforce_outbox` relay (own container) ──► SQS  ushspa-workforce-queue
EventBridge Scheduler  cron(0 1 * * ? *) Asia/Kuwait ────────► SQS  ushspa-workforce-queue
                                                                  │
ushanr  `python -m app.workers.workforce_worker` (own container) ◄┘
   ├─ workforce.*.changed      → wf_schedule_change_log
   └─ workforce.day_close.requested (01:00) → daily job for *yesterday*
         ├─ GET ushauth      /api/v1/internal/workforce/roster/?date=D
         ├─ GET ushbooknpay  /api/v1/internal/workforce/completed-sessions/?date=D   (status = completed ONLY)
         └─ wf_therapist_daily_roster / wf_completed_session /
            wf_therapist_activity_segment / wf_therapist_daily_summary
```

* Only **completed** bookings are work. Cancelled / no-show / confirmed / other statuses are ignored; their time stays idle.
* The job also (a) processes any of the last `WORKFORCE_DAY_CLOSE_CATCHUP_DAYS` (7) days that were never processed and
  (b) re-pulls completed sessions for the last `WORKFORCE_DAY_CLOSE_RECHECK_DAYS` (3) days so late completions are
  picked up. Re-checks keep the stored roster — history is never rewritten with today's schedule.
* Idempotent: re-running a day replaces that day's derived rows in one transaction (advisory-locked per date).

## Reporting API (ushanr, `/api/v1/workforce/…`, employee JWT)

| Endpoint | Purpose |
|---|---|
| `GET /therapists/{id}/daily/?from&to` | per-day roster + summary |
| `GET /therapists/{id}/timeline/?date=` | service / idle / break / leave / off segments with booking + service detail |
| `GET /therapists/{id}/schedule-history/?from&to&entity_type` | audit trail of every edit (before/after) |
| `GET /summary/?from&to&branch_id&therapist_id&group_by=day\|week\|month\|therapist` | aggregated minutes |
| `GET /utilization/?from&to&branch_id` | worked-vs-available leaderboard |
| `POST /internal/recompute/?date=&refresh_roster=` | repair (X-Internal-Key) |

Range is capped at 92 days.

## Configuration

> **Current Status (Paused / Disabled by Default):**
> The `workforce-relay` (in `ushauth`), `workforce-worker` (in `ushanr`), and the 1:00 AM daily job are **disabled by default** in Docker Compose and application settings so they do not run until you are ready to activate them.

| Service | Variable | Default | Notes |
|---|---|---|---|
| ushauth | `WORKFORCE_HISTORY_ENABLED` | `True` | Captures schedule edits locally in `outbox_events` (`False` disables capture) |
| ushauth | `WORKFORCE_RELAY_ENABLED` | `False` | Set `True` (and uncomment `workforce-relay` in `docker-compose.prod.yml`) to publish to SQS |
| ushauth | `AWS_SQS_WORKFORCE_QUEUE_URL` | `""` | Workforce SQS queue URL |
| ushanr | `WORKFORCE_CONSUMER_ENABLED` | `False` | Set `True` (and uncomment `workforce-worker` in `docker-compose*.yml`) to poll SQS |
| ushanr | `WORKFORCE_DAILY_JOB_ENABLED` | `False` | Set `True` to allow processing the 1:00 AM `workforce.day_close.requested` cron event |
| ushanr | `AWS_SQS_WORKFORCE_QUEUE_URL`, `AWS_REGION`, `AWS_ACCESS_KEY_ID/SECRET` | `""` | SQS credentials & queue URL |
| ushanr | `USHAUTH_BASE_URL`, `USHBOOKNPAY_BASE_URL` (+ `API_GATEWAY_BASE_URL`, `*_BASE_PATH`), `USHSPA_TOKEN` | — | Daily-job internal sources |

## Rollout

1. Create the AWS resources below (queue, DLQ, schedule).
2. ushanr: `alembic upgrade head` (migration `0007_workforce_history`), deploy `web` + `workforce-worker`.
3. ushbooknpay: deploy (new read-only internal endpoint).
4. ushauth: `python manage.py migrate`, deploy `web` + `workforce-relay`.
5. `python manage.py emit_schedule_baseline` (ushauth) once, to seed the change log with the current schedule.
6. Optional history backfill: `python -m app.workers.workforce_worker --run-day YYYY-MM-DD` per past day
   (roster of past days reflects *today's* rules → rows are approximate; bookings are exact).
7. Test the pipeline: `python -m app.workers.workforce_worker --run-yesterday`.

## Creating the 1:00 AM EventBridge Scheduler (Step-by-Step)

Since you already have your SQS queue and DLQ created, follow these steps to create the daily 1:00 AM (`Asia/Kuwait`) schedule that sends the `workforce.day_close.requested` message directly to your SQS queue.

### Prerequisite: Check your SQS Queue Settings
1. Open the **Amazon SQS Console** and select your existing queue.
2. Copy the **ARN** (e.g., `arn:aws:sqs:ap-south-1:123456789012:your-queue-name`) and **URL**.
3. Ensure **Visibility timeout** is set to at least **`300` seconds** (5 minutes) so the daily job has plenty of time to finish before SQS makes the message visible again.
4. Set `AWS_SQS_WORKFORCE_QUEUE_URL=<your-queue-url>` in both `ushauth/.env` and `ushanr/.env`.

---

### Option 1: Using the AWS Management Console (UI)

#### Step 1: Open EventBridge Scheduler
1. Log in to the **AWS Management Console** and verify you are in the same AWS Region as your SQS queue (e.g., `ap-south-1`).
2. Navigate to **Amazon EventBridge** → **Scheduler** → **Schedules** (in the left sidebar).
3. Click **Create schedule**.

#### Step 2: Specify Schedule Details
1. **Schedule name**: `ushspa-workforce-day-close`
2. **Description**: `Triggers ushanr daily therapist workforce history & analytics job at 1:00 AM Kuwait time.`
3. **Schedule group**: Leave as `default`.
4. Under **Schedule pattern**:
   - **Occurrence**: Select **Recurring schedule**.
   - **Time zone**: Select **`(UTC+03:00) Asia/Kuwait`**.
   - **Schedule type**: Select **Cron-based schedule**.
   - Enter the cron expression:
     - **Minutes**: `0`
     - **Hours**: `1`
     - **Day of month**: `*`
     - **Month**: `*`
     - **Day of week**: `?`
     - **Year**: `*`
     *(Full expression: `cron(0 1 * * ? *)` — runs every day at 01:00:00 AM Asia/Kuwait)*
   - **Flexible time window**: Select **Off** (so it fires at 01:00 sharp).
5. Click **Next**.

#### Step 3: Select Target (Amazon SQS)
1. Under **Target API**, select **Templated targets** and choose **Amazon SQS** (`SendMessage`).
2. Under **SQS queue**, select your existing SQS queue from the dropdown.
   *(Note: If you are using a `.fifo` queue, also fill in **Message group ID** as `workforce-day-close`; for a Standard queue, leave it blank).*
3. Under **Payload** (Message body), paste the following exact JSON:
   ```json
   {
     "event_id": "<aws.scheduler.execution-id>",
     "event_type": "workforce.day_close.requested",
     "version": 1,
     "occurred_at": "<aws.scheduler.scheduled-time>",
     "source": "aws.scheduler",
     "data": {}
   }
   ```
   > **Note:** `<aws.scheduler.execution-id>` and `<aws.scheduler.scheduled-time>` are built-in EventBridge Scheduler context variables that AWS automatically replaces at runtime with a unique UUID and ISO-8601 timestamp. When `data.date` is omitted, the `ushanr` worker automatically computes yesterday's date in `Asia/Kuwait` from `occurred_at`.
4. Click **Next**.

#### Step 4: Configure Settings, DLQ & Permissions
1. **Schedule state**: Ensure **Enable** is toggled on.
2. **Action after schedule completion**: Select **NONE**.
3. **Retry policy and dead-letter queue (DLQ)**:
   - Keep **Retry policy** enabled (or set Maximum age of event to `1 hour`, retry attempts to `5`).
   - Under **Dead-letter queue**, select **Amazon SQS** and choose your existing **DLQ** so any delivery failure from Scheduler to SQS is captured.
4. **Permissions (Execution role)**:
   - Select **Create new role for this schedule** and name it `ushspa-workforce-scheduler-role` (AWS will automatically grant `sqs:SendMessage` on your target queue and DLQ).
   - *Or*, if you prefer to use an existing IAM role, ensure it has the trust policy and permissions shown in Option 2 below.
5. Click **Next**, review all settings, and click **Create schedule**.

---

### Option 2: Using the AWS CLI

Replace `<REGION>`, `<ACCOUNT_ID>`, `<QUEUE_NAME>`, and `<DLQ_NAME>` with your existing values:

#### 1. Create the IAM Execution Role for EventBridge Scheduler
```bash
cat > scheduler-trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "scheduler.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name ushspa-workforce-scheduler-role \
  --assume-role-policy-document file://scheduler-trust-policy.json

cat > scheduler-sqs-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["sqs:SendMessage"],
      "Resource": [
        "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<QUEUE_NAME>",
        "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<DLQ_NAME>"
      ]
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name ushspa-workforce-scheduler-role \
  --policy-name AllowSQSSendMessage \
  --policy-document file://scheduler-sqs-policy.json
```

#### 2. Create the Daily 1:00 AM Schedule
```bash
aws scheduler create-schedule \
  --region <REGION> \
  --name ushspa-workforce-day-close \
  --description "Daily 1:00 AM Asia/Kuwait trigger for ushanr workforce history job" \
  --schedule-expression "cron(0 1 * * ? *)" \
  --schedule-expression-timezone "Asia/Kuwait" \
  --flexible-time-window '{"Mode": "OFF"}' \
  --state ENABLED \
  --target '{
    "Arn": "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<QUEUE_NAME>",
    "RoleArn": "arn:aws:iam::<ACCOUNT_ID>:role/ushspa-workforce-scheduler-role",
    "Input": "{\"event_id\":\"<aws.scheduler.execution-id>\",\"event_type\":\"workforce.day_close.requested\",\"version\":1,\"occurred_at\":\"<aws.scheduler.scheduled-time>\",\"source\":\"aws.scheduler\",\"data\":{}}",
    "DeadLetterConfig": {
      "Arn": "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<DLQ_NAME>"
    },
    "RetryPolicy": {
      "MaximumEventAgeInSeconds": 3600,
      "MaximumRetryAttempts": 5
    }
  }'
```

---

### How to Test & Verify Immediately
You don't have to wait until 1:00 AM to test that the queue and worker are wired properly:

1. **Test via SQS Send Message (Console or CLI)**:
   In the SQS Console → your queue → **Send and receive messages**, paste this message body and click **Send message**:
   ```json
   {
     "event_id": "00000000-0000-0000-0000-000000000001",
     "event_type": "workforce.day_close.requested",
     "version": 1,
     "occurred_at": "2026-11-06T01:00:00+03:00",
     "source": "manual.test",
     "data": { "date": "2026-11-05" }
   }
   ```
2. **Check the `ushanr` worker logs**:
   ```bash
   docker logs -f ushanr-workforce-worker
   ```
   You should see `workforce_day_closed` with the count of therapists, sessions, and segments written to `ushanr`.

---

## AWS resources (CloudFormation reference)

```yaml
Resources:
  WorkforceDLQ:
    Type: AWS::SQS::Queue
    Properties: { QueueName: ushspa-workforce-dlq, MessageRetentionPeriod: 1209600 }
  WorkforceQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: ushspa-workforce-queue
      VisibilityTimeout: 300            # >= WORKFORCE_SQS_VISIBILITY_TIMEOUT
      ReceiveMessageWaitTimeSeconds: 20
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt WorkforceDLQ.Arn
        maxReceiveCount: 5
  DLQDepthAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      Namespace: AWS/SQS
      MetricName: ApproximateNumberOfMessagesVisible
      Dimensions: [{ Name: QueueName, Value: ushspa-workforce-dlq }]
      Statistic: Maximum
      Period: 300
      EvaluationPeriods: 1
      Threshold: 0
      ComparisonOperator: GreaterThanThreshold
  SchedulerRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Statement: [{ Effect: Allow, Principal: { Service: scheduler.amazonaws.com }, Action: sts:AssumeRole }]
      Policies:
        - PolicyName: send
          PolicyDocument:
            Statement: [{ Effect: Allow, Action: sqs:SendMessage, Resource: !GetAtt WorkforceQueue.Arn }]
  DailyDayClose:
    Type: AWS::Scheduler::Schedule
    Properties:
      Name: ushspa-workforce-day-close
      ScheduleExpression: cron(0 1 * * ? *)
      ScheduleExpressionTimezone: Asia/Kuwait
      FlexibleTimeWindow: { Mode: "OFF" }
      Target:
        Arn: !GetAtt WorkforceQueue.Arn
        RoleArn: !GetAtt SchedulerRole.Arn
        Input: >-
          {"event_id":"<aws.scheduler.execution-id>","event_type":"workforce.day_close.requested",
           "occurred_at":"<aws.scheduler.scheduled-time>","data":{}}
```

IAM: ushauth relay needs `sqs:SendMessage` on the queue; the ushanr worker needs
`sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`.
