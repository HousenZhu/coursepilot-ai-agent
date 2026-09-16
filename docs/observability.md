# Local Observability

The standalone demo has an optional Prometheus and read-only Grafana dashboard overlay.
Start the isolated demo first, then from this repository root run:

```bash
docker compose -f docker-compose.eval.yml -f docker-compose.observability.yml --profile observability up -d prometheus grafana
```

Open `http://localhost:3001/d/coursepilot-overview`. Both ports are bound to loopback.
Anonymous access is viewer-only for a local demo; do not expose these services publicly.
The dashboard scrapes the demo's `eval-agent` and needs requests before panels have samples.
An empty panel is not zero latency or a zero error rate.

Panels cover execution outcomes, first validated segment p50/p95, stage p95, tool status,
completed-run latency and retrieved chunk counts. A completed execution can still be an
incorrect answer; model quality belongs in the evaluation report, not the HTTP metrics.
The request histogram currently covers completed runs only. Histogram quantiles are
bucket estimates; the evaluation runner reports percentiles from individual requests.

For traces, configure `OTEL_EXPORTER_OTLP_ENDPOINT` with your OTLP gRPC collector address.
Node spans cover validation, route, tool dispatch and answer verification, with HTTP and
database instrumentation. No trace collector is bundled or required for normal startup.
The application `trace_id` stored on a run is a correlation ID, not a promise that it
equals the OpenTelemetry trace ID. Avoid collecting request bodies or sensitive headers.

The dashboard is an operational aid, not a load-test result. Real model measurements
must record background workloads, hardware, model digest and actual context length.
