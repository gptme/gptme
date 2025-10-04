# Pushgateway Setup - Step by Step

## 1. Access server3

```bash
ssh erb@server3
```

## 2. Find and access the Prometheus container

```bash
# List containers to find Prometheus
pct list

# Access the container (replace XXX with container ID)
pct enter XXX
```

## 3. Start Pushgateway in the Prometheus container

```bash
# Option A: If Docker is available in container
docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  --restart unless-stopped \
  prom/pushgateway:latest \
  --push.disable-consistency-check \
  --metric.expiration=3600s

# Option B: Download and run binary
wget https://github.com/prometheus/pushgateway/releases/download/v1.10.0/pushgateway-1.10.0.linux-amd64.tar.gz
tar xvf pushgateway-1.10.0.linux-amd64.tar.gz
cd pushgateway-1.10.0.linux-amd64
nohup ./pushgateway --push.disable-consistency-check --metric.expiration=3600s &
```

## 4. Update Prometheus config

```bash
# Edit prometheus config
nano /etc/prometheus/prometheus.yml

# Add this to scrape_configs:
# - job_name: 'gptme-pushgateway'
#   honor_labels: true
#   static_configs:
#     - targets: ['localhost:9091']

# Reload Prometheus
systemctl reload prometheus
# OR
killall -HUP prometheus
```

## 5. Test from Bob's VM

Exit back to Bob's VM and test:

```bash
# Test push
echo "test_metric 42" | curl --data-binary @- http://192.168.1.65:9091/metrics/job/test

# Check it's there
curl http://192.168.1.65:9091/metrics | grep test_metric

# Clean up test
curl -X DELETE http://192.168.1.65:9091/metrics/job/test
```

## 6. Update Bob's .profile

Already done! The PUSHGATEWAY_URL is commented in `.profile`:

```bash
source ~/.profile
```

## Notes

- Prometheus container IP is 192.168.1.65 on your network
- Port 9091 is the standard Pushgateway port
- The `--metric.expiration=3600s` cleans up old metrics after 1 hour
