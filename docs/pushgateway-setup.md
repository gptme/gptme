# Pushgateway Setup Guide

## Overview

Pushgateway allows gptme instances to push metrics to a central location that Prometheus can scrape.

## Architecture

```txt
Bob's VM (and other machines):
  gptme instance 1 ──┐
  gptme instance 2 ──┼──> Pushgateway:9091 ──> Prometheus:9090
  gptme instance 3 ──┘       (192.168.1.65)       (192.168.1.65)
```

## Installation Options

### Option 1: Docker on server3 (Recommended)

On your Prometheus server (192.168.1.65):

```bash
# Run Pushgateway container
docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  --restart unless-stopped \
  prom/pushgateway:latest

# Verify it's running
curl http://localhost:9091/metrics
```

### Option 2: Standalone Binary

```bash
# Download latest release
wget https://github.com/prometheus/pushgateway/releases/download/v1.10.0/pushgateway-1.10.0.linux-amd64.tar.gz
tar xvf pushgateway-1.10.0.linux-amd64.tar.gz
cd pushgateway-1.10.0.linux-amd64

# Run in background
./pushgateway &
```

### Option 3: systemd Service

```bash
# Create service file
sudo tee /etc/systemd/system/pushgateway.service <<EOF
[Unit]
Description=Prometheus Pushgateway
After=network.target

[Service]
Type=simple
User=prometheus
ExecStart=/usr/local/bin/pushgateway
Restart=always

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now pushgateway
```

## Configure Prometheus

Add to your Prometheus config (`prometheus.yml`):

```yaml
scrape_configs:
  # Your existing scrape configs here

  - job_name: 'gptme-pushgateway'
    honor_labels: true  # Important: preserve instance labels
    static_configs:
      - targets: ['localhost:9091']  # or 192.168.1.65:9091
```

Then reload Prometheus:

```bash
# If using systemd
sudo systemctl reload prometheus

# If using Docker
docker kill -s HUP prometheus

# Or via API
curl -X POST http://localhost:9090/-/reload
```

## Quick Test (Manual Push)

Before implementing in gptme, test the Pushgateway:

```bash
# Push a test metric
echo "test_metric 42" | curl --data-binary @- http://192.168.1.65:9091/metrics/job/test

# Check it was received
curl http://192.168.1.65:9091/metrics | grep test_metric

# Clean up
curl -X DELETE http://192.168.1.65:9091/metrics/job/test
```

## Implementation Plan for gptme

### Phase 1: Environment Variable Support

Add `PUSHGATEWAY_URL` support to `gptme/util/_telemetry.py`.

Key changes needed:
1. Check for `PUSHGATEWAY_URL` environment variable
2. If set, use Pushgateway instead of HTTP server
3. Set up periodic pushing (every 30-60 seconds)
4. Handle graceful shutdown with final push

### Phase 2: Full Integration (Future PR)

Features to add:
- Automatic retry logic
- Configuration validation
- Health checks
- Metric expiry coordination
- Documentation updates

## Usage After Implementation

### For Bob's VM

Update `.profile`:

```bash
export GPTME_TELEMETRY_ENABLED=true
export OTLP_ENDPOINT=http://192.168.1.65:4317
export PUSHGATEWAY_URL=http://192.168.1.65:9091

# These are no longer needed with Pushgateway:
# export PROMETHEUS_ADDR="0.0.0.0"
# export PROMETHEUS_PORT="8100"
```

Reload profile:

```bash
source ~/.profile
```

### Testing

```bash
# Start a gptme instance
gptme 'print("hello")'

# Check metrics were pushed
curl http://192.168.1.65:9091/metrics | grep gptme

# Check Prometheus received them
curl 'http://192.168.1.65:9090/api/v1/query?query=gptme_tokens_total'
```

## Advantages

✅ **No port conflicts**: All instances push to same endpoint
✅ **Auto-discovery**: Prometheus scrapes one endpoint
✅ **Simple setup**: Standard Prometheus component
✅ **Persistent metrics**: Gateway retains last pushed values
✅ **Works remotely**: Push over network, no firewall issues

## Considerations

⚠️ **Metric persistence**: Old instances stay in gateway until cleared
⚠️ **Push frequency**: Balance between freshness and overhead
⚠️ **Label cardinality**: Each PID creates new metric series

### Cleanup Strategy

Metrics persist in Pushgateway. Choose an approach:

**Option 1: Automatic expiry** (Pushgateway 1.10+):
```bash
pushgateway --push.disable-consistency-check --metric.expiration=300s
```

**Option 2: Manual cleanup**:
```bash
# Delete specific job
curl -X DELETE http://192.168.1.65:9091/metrics/job/gptme-12345

# Delete all gptme jobs (requires script to iterate)
for job in $(curl -s http://192.168.1.65:9091/metrics | grep job=\"gptme | cut -d'"' -f2 | sort -u); do
  curl -X DELETE http://192.168.1.65:9091/metrics/job/$job
done
```

**Option 3: Periodic cleanup cron**:
```bash
# Add to crontab: clean up metrics every hour
0 * * * * /home/bob/scripts/cleanup-pushgateway.sh
```

Example cleanup script:
```bash
#!/bin/bash
# cleanup-pushgateway.sh
# Remove gptme metrics older than 1 hour

GATEWAY="http://192.168.1.65:9091"

# This is a simple version - production needs timestamp tracking
curl -X DELETE "$GATEWAY/metrics/job/gptme-*"
```

## Immediate Action Items

### 1. Set up Pushgateway on 192.168.1.65

```bash
# SSH to your Prometheus server
ssh user@192.168.1.65

# Start Pushgateway with Docker
docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  --restart unless-stopped \
  prom/pushgateway:latest \
  --push.disable-consistency-check \
  --metric.expiration=3600s
```

### 2. Update Prometheus config

```bash
# Edit prometheus.yml and add the scrape config shown above
# Then reload Prometheus
```

### 3. Test manually

```bash
# From Bob's VM
echo "test_metric 42" | curl --data-binary @- http://192.168.1.65:9091/metrics/job/test
curl http://192.168.1.65:9091/metrics | grep test_metric
```

### 4. Future: Implement in gptme

This will be a follow-up PR to add native Pushgateway support.

## Resources

- [Pushgateway GitHub](https://github.com/prometheus/pushgateway)
- [Prometheus Docs](https://prometheus.io/docs/practices/pushing/)
- [Best Practices](https://prometheus.io/docs/practices/instrumentation/)
- [When to use Pushgateway](https://prometheus.io/docs/practices/pushing/#should-i-be-using-the-pushgateway)
