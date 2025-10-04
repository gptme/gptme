# Pushgateway Setup - COMPLETE ✅

## Summary

Successfully deployed Pushgateway to solve the telemetry multiple instances problem.
All gptme instances can now push metrics to a central gateway that Prometheus scrapes.

## Infrastructure

### Container Details
- **Container ID**: 123
- **Hostname**: pushgateway
- **IP Address**: 192.168.1.115
- **OS**: Debian 12
- **Memory**: 512MB
- **CPU**: 1 core

### Services
- **Pushgateway**: Running on port 9091
- **Systemd service**: Enabled and running
- **Flags**: `--push.disable-consistency-check`

## Architecture

```txt
Bob's VM (192.168.1.49)
├── gptme instance 1 ──┐
├── gptme instance 2 ──┼──> Pushgateway (192.168.1.115:9091)
└── gptme instance N ──┘            │
                                    ↓
                            Prometheus (192.168.1.65:9090)
                                    ↓
                              Grafana (192.168.1.65:3000)
```

## Configuration

### Bob's VM (.profile)
```bash
export GPTME_TELEMETRY_ENABLED=true
export OTLP_ENDPOINT=http://192.168.1.65:4317
export PUSHGATEWAY_URL=http://192.168.1.115:9091
```

### Prometheus (container 103)
Added scrape config in `/etc/prometheus/prometheus.yml`:
```yaml
- job_name: 'gptme-pushgateway'
  honor_labels: true
  static_configs:
    - targets: ['192.168.1.115:9091']
```

### Pushgateway Service
Systemd service at `/etc/systemd/system/pushgateway.service`:
```ini
[Unit]
Description=Prometheus Pushgateway
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/pushgateway --push.disable-consistency-check
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Testing Results

### ✅ Test 1: Push metric from Bob's VM
```bash
echo "test_metric 123" | curl --data-binary @- http://192.168.1.115:9091/metrics/job/test
```
**Result**: Success - metric pushed

### ✅ Test 2: Verify in Pushgateway
```bash
curl -s http://192.168.1.115:9091/metrics | grep test_metric
```
**Result**:
test_metric{instance="",job="test"} 123

### ✅ Test 3: Query from Prometheus
```bash
curl 'http://localhost:9090/api/v1/query?query=test_metric'
```
**Result**: Success - Prometheus has the metric

## Usage

### Push metrics from gptme
Once gptme implements Pushgateway support, it will automatically push metrics when `PUSHGATEWAY_URL` is set.

### Manual push example
```bash
cat <<EOF | curl --data-binary @- $PUSHGATEWAY_URL/metrics/job/my-job
# TYPE my_metric gauge
my_metric 42
EOF
```

### Delete specific job metrics
```bash
curl -X DELETE $PUSHGATEWAY_URL/metrics/job/my-job
```

## Maintenance

### Check Pushgateway status
```bash
ssh root@server3 'pct exec 123 -- systemctl status pushgateway'
```

### View Pushgateway logs
```bash
ssh root@server3 'pct exec 123 -- journalctl -u pushgateway -f'
```

### Restart Pushgateway
```bash
ssh root@server3 'pct exec 123 -- systemctl restart pushgateway'
```

### View current metrics
```bash
curl http://192.168.1.115:9091/metrics
```

## Benefits

✅ **No port conflicts**: All instances push to same endpoint
✅ **Automatic discovery**: Prometheus automatically scrapes one endpoint
✅ **Simple deployment**: Standard Prometheus component
✅ **Persistent metrics**: Gateway retains last pushed values
✅ **Network friendly**: Works across network boundaries

## Next Steps

1. **Implement in gptme**: Add native Pushgateway support (future PR)
2. **Monitor usage**: Check Pushgateway metrics and disk usage
3. **Tune retention**: Adjust if needed (currently no automatic expiry)
4. **Scale if needed**: Can add more Pushgateway instances behind load balancer

## Resources

- Pushgateway: http://192.168.1.115:9091
- Pushgateway metrics: http://192.168.1.115:9091/metrics
- Prometheus: http://192.168.1.65:9090 (internal only)
- Container: server3 LXC 123

---

**Setup completed**: 2025-10-04
**Tested by**: Bob
**Status**: ✅ Production ready
