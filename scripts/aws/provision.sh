#!/bin/sh
# Create the pokbot Lightsail instance (idempotent): key pair, instance, static IP, firewall,
# and optionally a $1 AWS Budgets alert. Prints the public host to use with deploy.sh.
#
#   AWS_REGION=us-east-1 BUNDLE=small_3_0 POKBOT_ALERT_EMAIL=you@example.com scripts/aws/provision.sh
set -eu
cd "$(dirname "$0")/../.."

REGION="${AWS_REGION:-us-east-1}"
ZONE="${ZONE:-${REGION}a}"
NAME="${NAME:-pokbot}"
BUNDLE="${BUNDLE:-small_3_0}"            # $12/month: 2 GB RAM, 2 vCPU, 60 GB SSD
BLUEPRINT="${BLUEPRINT:-ubuntu_24_04}"
KEY_NAME="${NAME}-deploy"
KEY_FILE="${KEY_FILE:-$HOME/.ssh/${NAME}-lightsail.pem}"
lsail() { aws lightsail --region "$REGION" "$@"; }

echo "◆ provisioning $NAME in $REGION ($BUNDLE)"

lsail get-bundles --query "bundles[?bundleId=='$BUNDLE'].price" --output text | grep -q . \
  || { echo "unknown bundle $BUNDLE; list: aws lightsail get-bundles" >&2; exit 1; }

if ! lsail get-key-pair --key-pair-name "$KEY_NAME" >/dev/null 2>&1; then
  [ -e "$KEY_FILE" ] && { echo "$KEY_FILE exists but key pair $KEY_NAME does not; move it aside" >&2; exit 1; }
  mkdir -p "$(dirname "$KEY_FILE")"
  lsail create-key-pair --key-pair-name "$KEY_NAME" --query privateKeyBase64 --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "✓ key pair $KEY_NAME → $KEY_FILE"
fi

if ! lsail get-instance --instance-name "$NAME" >/dev/null 2>&1; then
  lsail create-instances --instance-names "$NAME" --availability-zone "$ZONE" \
    --blueprint-id "$BLUEPRINT" --bundle-id "$BUNDLE" --key-pair-name "$KEY_NAME" \
    --user-data file://deploy/bootstrap.sh --tags key=app,value=pokbot >/dev/null
  echo "✓ instance $NAME created"
fi

printf 'waiting for instance to run'
until [ "$(lsail get-instance --instance-name "$NAME" --query instance.state.name --output text)" = running ]; do
  printf '.'; sleep 5
done
echo

if ! lsail get-static-ip --static-ip-name "$NAME-ip" >/dev/null 2>&1; then
  lsail allocate-static-ip --static-ip-name "$NAME-ip" >/dev/null
fi
lsail attach-static-ip --static-ip-name "$NAME-ip" --instance-name "$NAME" >/dev/null 2>&1 || true
IP="$(lsail get-static-ip --static-ip-name "$NAME-ip" --query staticIp.ipAddress --output text)"
echo "✓ static IP $IP"

MYIP="$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')"
lsail put-instance-public-ports --instance-name "$NAME" --port-infos \
  "fromPort=80,toPort=80,protocol=tcp,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" \
  "fromPort=443,toPort=443,protocol=tcp,cidrs=0.0.0.0/0,ipv6Cidrs=::/0" \
  "fromPort=22,toPort=22,protocol=tcp,cidrs=$MYIP/32" >/dev/null
echo "✓ firewall: 80/443 open, 22 only from $MYIP"

if [ -n "${POKBOT_ALERT_EMAIL:-}" ]; then
  ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
  if ! aws budgets describe-budget --account-id "$ACCOUNT" --budget-name pokbot-real-spend >/dev/null 2>&1; then
    aws budgets create-budget --account-id "$ACCOUNT" \
      --budget '{"BudgetName":"pokbot-real-spend","BudgetLimit":{"Amount":"1","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST","CostTypes":{"IncludeCredit":false}}' \
      --notifications-with-subscribers "[{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$POKBOT_ALERT_EMAIL\"}]}]"
    echo "✓ budget alert: email when real (non-credit) spend passes \$1/month"
  fi
fi

HOST="$(echo "$IP" | tr . -).sslip.io"
echo
echo "host: $HOST   (sslip.io maps it to $IP; swap in your own domain later)"
echo "next: POKBOT_HOST=$HOST scripts/aws/deploy.sh"
