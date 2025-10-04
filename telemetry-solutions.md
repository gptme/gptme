# Telemetry Solutions for Multiple Instances

## Problem
Current PR #668 enables multiple gptme instances to run by finding available ports,
but Prometheus can only scrape pre-configured ports.

## Architecture Comparison

### Current State
```yaml
OTLP Tracing:  ✅ All instances → Central endpoint (push)
Prometheus:    ⚠️  Each instance → Separate HTTP port (pull)
```

### Solution Options

#### 1. Prometheus Pushgateway ⭐ RECOMMENDED
**Pros:**
- Standard solution for dynamic/short-lived processes
- Simple to implement (~50 lines of code)
- No Prometheus config changes needed
- Works with existing infrastructure

**Cons:**
- Requires running Pushgateway service
- Metrics persist in gateway (need expiry strategy)

**Implementation:**
```python
# Replace PrometheusMetricReader with push to gateway
from prometheus_client import CollectorRegistry, push_to_gateway

def push_metrics_to_gateway():
    gateway_url = os.getenv("PUSHGATEWAY_URL", "http://192.168.1.65:9091")
    job_name = f"gptme-{os.getpid()}"
    push_to_gateway(gateway_url, job=job_name, registry=registry)
```

**Architecture:**
```txt
gptme-8000 ─┐
gptme-8001 ─┼─→ Pushgateway:9091 ←─ Prometheus
gptme-8002 ─┘
```

#### 2. OTLP Metrics + OpenTelemetry Collector
**Pros:**
- Modern, unified approach (traces + metrics via OTLP)
- Most flexible and extensible
- Already using OTLP for traces

**Cons:**
- Requires OpenTelemetry Collector setup
- More complex configuration
- Additional infrastructure component

**Implementation:**
```python
# Add OTLP metric exporter alongside span exporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

otlp_metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint)
metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter)
```

**Architecture:**
```txt
gptme-* ─→ OTLP Collector ─→ Prometheus
                           └─→ Jaeger
```

#### 3. File-based Service Discovery
**Pros:**
- Native Prometheus feature
- Good for long-running processes
- No additional services needed

**Cons:**
- Requires filesystem coordination
- Cleanup of stale entries needed
- More complex than Pushgateway

**Implementation:**
```python
# Write port info to discovery file
discovery_file = "/tmp/gptme-prometheus-targets.json"
with open(discovery_file, 'a') as f:
    json.dump([{
        "targets": [f"{prometheus_addr}:{prometheus_port}"],
        "labels": {"instance": f"gptme-{os.getpid()}"}
    }], f)
```

**Prometheus config:**
```yaml
scrape_configs:
  - job_name: 'gptme-dynamic'
    file_sd_configs:
      - files:
          - '/tmp/gptme-prometheus-targets.json'
        refresh_interval: 5s
```

#### 4. Document Limitation (Current State)
**Pros:**
- No code changes needed
- Simple to understand

**Cons:**
- Only configured ports get metrics
- User must manually add ports to Prometheus config

## Recommendation

For Bob's use case (multiple autonomous instances on VM):

**Short term:** Document the limitation in PR
- Note that only OTLP tracing works for all instances
- Prometheus metrics only available on configured ports

**Medium term:** Implement Pushgateway support
- Add optional `PUSHGATEWAY_URL` environment variable
- Fall back to HTTP server if not set
- Maintains backward compatibility

**Long term:** Consider OTLP metrics
- Most modern and flexible
- Unified telemetry pipeline
- Requires collector setup but worth it for production

## Implementation Plan

1. **Add to PR description:**
   - Document Prometheus limitation
   - Recommend Pushgateway for production
   - Note OTLP tracing works for all instances

2. **Optional follow-up PR:**
   - Add Pushgateway support
   - Keep HTTP server as fallback
   - Add documentation

3. **Update .profile example:**
```bash
export GPTME_TELEMETRY_ENABLED=true
export OTLP_ENDPOINT=http://192.168.1.65:4317
# For Prometheus metrics from all instances:
export PUSHGATEWAY_URL=http://192.168.1.65:9091
# OR for specific instance on fixed port:
export PROMETHEUS_ADDR="0.0.0.0"
export PROMETHEUS_PORT="8100"
```
