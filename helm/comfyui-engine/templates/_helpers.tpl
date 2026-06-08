{{- /*
  ComfyUI Engine Helm Chart Helpers
  Common templates and functions used throughout the chart
*/ -}}

{{/*
Expand the name of the chart.
*/}}
{{- define "comfyui-engine.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "comfyui-engine.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "comfyui-engine.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "comfyui-engine.labels" -}}
helm.sh/chart: {{ include "comfyui-engine.chart" . }}
{{ include "comfyui-engine.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "comfyui-engine.selectorLabels" -}}
app.kubernetes.io/name: {{ include "comfyui-engine.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "comfyui-engine.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "comfyui-engine.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the secret to use for JWT
*/}}
{{- define "comfyui-engine.jwtSecretName" -}}
{{- if .Values.secrets.jwt.existingSecret }}
{{- .Values.secrets.jwt.existingSecret }}
{{- else }}
{{- printf "%s-jwt" (include "comfyui-engine.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Create the name of the secret to use for database
*/}}
{{- define "comfyui-engine.dbSecretName" -}}
{{- if .Values.secrets.db.existingSecret }}
{{- .Values.secrets.db.existingSecret }}
{{- else }}
{{- printf "%s-db" (include "comfyui-engine.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Get the Redis URL
*/}}
{{- define "comfyui-engine.redisUrl" -}}
{{- if .Values.redis.enabled }}
{{- printf "redis://%s-redis-master:6379/0" (include "comfyui-engine.fullname" .) }}
{{- else }}
{{- .Values.config.redis.url }}
{{- end }}
{{- end }}

{{/*
Get the database host
*/}}
{{- define "comfyui-engine.dbHost" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" (include "comfyui-engine.fullname" .) }}
{{- else }}
{{- .Values.config.database.host }}
{{- end }}
{{- end }}

{{/*
Get the database password from secret
*/}}
{{- define "comfyui-engine.dbPassword" -}}
{{- if .Values.postgresql.enabled }}
{{- if .Values.postgresql.auth.existingSecret }}
{{- printf "${%s}" .Values.postgresql.auth.existingSecret }}
{{- else }}
{{- printf "${%s-db}" (include "comfyui-engine.fullname" .) }}
{{- end }}
{{- else }}
{{- .Values.config.database.password }}
{{- end }}
{{- end }}

{{/*
GPU resource configuration
*/}}
{{- define "comfyui-engine.gpuResources" -}}
{{- if .Values.gpu.enabled }}
nvidia.com/gpu: {{ .Values.gpu.count | quote }}
{{- end }}
{{- end }}

{{/*
Node selector for GPU nodes
*/}}
{{- define "comfyui-engine.nodeSelector" -}}
{{- if .Values.gpu.enabled }}
{{- merge .Values.nodeSelector (dict "nvidia.com/gpu.present" "true") | toYaml }}
{{- else }}
{{- .Values.nodeSelector | toYaml }}
{{- end }}
{{- end }}

{{/*
Tolerations for GPU taints
*/}}
{{- define "comfyui-engine.tolerations" -}}
{{- if .Values.gpu.enabled }}
{{- concat .Values.tolerations (list (dict "key" "nvidia.com/gpu" "operator" "Exists" "effect" "NoSchedule")) | toYaml }}
{{- else }}
{{- .Values.tolerations | toYaml }}
{{- end }}
{{- end }}

{{/*
Affinity configuration
*/}}
{{- define "comfyui-engine.affinity" -}}
{{- if .Values.affinity }}
{{- .Values.affinity | toYaml }}
{{- end }}
{{- end }}

{{/*
Pod topology spread constraints
*/}}
{{- define "comfyui-engine.topologySpreadConstraints" -}}
{{- if .Values.topologySpreadConstraints }}
{{- .Values.topologySpreadConstraints | toYaml }}
{{- end }}
{{- end }}

{{/*
Priority class name
*/}}
{{- define "comfyui-engine.priorityClassName" -}}
{{- if .Values.priorityClassName }}
priorityClassName: {{ .Values.priorityClassName }}
{{- end }}
{{- end }}

{{/*
Termination grace period
*/}}
{{- define "comfyui-engine.terminationGracePeriodSeconds" -}}
{{- if .Values.terminationGracePeriodSeconds }}
terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
{{- end }}
{{- end }}

{{/*
DNS policy
*/}}
{{- define "comfyui-engine.dnsPolicy" -}}
{{- if .Values.dnsPolicy }}
dnsPolicy: {{ .Values.dnsPolicy }}
{{- end }}
{{- end }}

{{/*
DNS config
*/}}
{{- define "comfyui-engine.dnsConfig" -}}
{{- if .Values.dnsConfig }}
dnsConfig:
  {{- .Values.dnsConfig | toYaml | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Host aliases
*/}}
{{- define "comfyui-engine.hostAliases" -}}
{{- if .Values.hostAliases }}
hostAliases:
  {{- .Values.hostAliases | toYaml | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Host network
*/}}
{{- define "comfyui-engine.hostNetwork" -}}
{{- if .Values.hostNetwork }}
hostNetwork: {{ .Values.hostNetwork }}
{{- end }}
{{- end }}

{{/*
Host PID
*/}}
{{- define "comfyui-engine.hostPID" -}}
{{- if .Values.hostPID }}
hostPID: {{ .Values.hostPID }}
{{- end }}
{{- end }}

{{/*
Host IPC
*/}}
{{- define "comfyui-engine.hostIPC" -}}
{{- if .Values.hostIPC }}
hostIPC: {{ .Values.hostIPC }}
{{- end }}
{{- end }}

{{/*
Security context for the pod
*/}}
{{- define "comfyui-engine.podSecurityContext" -}}
{{- if .Values.podSecurityContext }}
securityContext:
  {{- .Values.podSecurityContext | toYaml | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Security context for containers
*/}}
{{- define "comfyui-engine.containerSecurityContext" -}}
{{- if .Values.securityContext }}
securityContext:
  {{- .Values.securityContext | toYaml | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Extra environment variables
*/}}
{{- define "comfyui-engine.extraEnv" -}}
{{- if .Values.extraEnv }}
{{- range .Values.extraEnv }}
- name: {{ .name }}
  value: {{ .value | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Extra environment variables from config maps or secrets
*/}}
{{- define "comfyui-engine.extraEnvFrom" -}}
{{- if .Values.extraEnvFrom }}
{{- .Values.extraEnvFrom | toYaml }}
{{- end }}
{{- end }}

{{/*
Extra volumes
*/}}
{{- define "comfyui-engine.extraVolumes" -}}
{{- if .Values.extraVolumes }}
{{- .Values.extraVolumes | toYaml }}
{{- end }}
{{- end }}

{{/*
Extra volume mounts
*/}}
{{- define "comfyui-engine.extraVolumeMounts" -}}
{{- if .Values.extraVolumeMounts }}
{{- .Values.extraVolumeMounts | toYaml }}
{{- end }}
{{- end }}

{{/*
Extra containers
*/}}
{{- define "comfyui-engine.extraContainers" -}}
{{- if .Values.extraContainers }}
{{- .Values.extraContainers | toYaml }}
{{- end }}
{{- end }}

{{/*
Extra init containers
*/}}
{{- define "comfyui-engine.extraInitContainers" -}}
{{- if .Values.extraInitContainers }}
{{- .Values.extraInitContainers | toYaml }}
{{- end }}
{{- end }}

{{/*
Extra args
*/}}
{{- define "comfyui-engine.extraArgs" -}}
{{- if .Values.extraArgs }}
{{- range .Values.extraArgs }}
- {{ . | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Extra labels
*/}}
{{- define "comfyui-engine.extraLabels" -}}
{{- if .Values.extraLabels }}
{{- range $key, $value := .Values.extraLabels }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Extra annotations
*/}}
{{- define "comfyui-engine.extraAnnotations" -}}
{{- if .Values.extraAnnotations }}
{{- range $key, $value := .Values.extraAnnotations }}
{{ $key }}: {{ $value | quote }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Pod annotations
*/}}
{{- define "comfyui-engine.podAnnotations" -}}
{{- if .Values.podAnnotations }}
{{- .Values.podAnnotations | toYaml }}
{{- end }}
{{- end }}

{{/*
Pod labels
*/}}
{{- define "comfyui-engine.podLabels" -}}
{{- if .Values.podLabels }}
{{- .Values.podLabels | toYaml }}
{{- end }}
{{- end }}

{{/*
Service annotations
*/}}
{{- define "comfyui-engine.serviceAnnotations" -}}
{{- if .Values.service.annotations }}
{{- .Values.service.annotations | toYaml }}
{{- end }}
{{- end }}

{{/*
Ingress annotations
*/}}
{{- define "comfyui-engine.ingressAnnotations" -}}
{{- if .Values.ingress.annotations }}
{{- .Values.ingress.annotations | toYaml }}
{{- end }}
{{- end }}

{{/*
Ingress TLS
*/}}
{{- define "comfyui-engine.ingressTLS" -}}
{{- if .Values.ingress.tls }}
tls:
  {{- .Values.ingress.tls | toYaml | nindent 2 }}
{{- end }}
{{- end }}

{{/*
Ingress hosts
*/}}
{{- define "comfyui-engine.ingressHosts" -}}
{{- if .Values.ingress.hosts }}
{{- range .Values.ingress.hosts }}
- host: {{ .host | quote }}
  http:
    paths:
      {{- range .paths }}
      - path: {{ .path | quote }}
        pathType: {{ .pathType | default "Prefix" | quote }}
        backend:
          service:
            name: {{ include "comfyui-engine.fullname" $ }}
            port:
              number: {{ $.Values.service.port }}
      {{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Persistent volume claim for models
*/}}
{{- define "comfyui-engine.modelsPVC" -}}
{{- if .Values.persistence.models.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-models
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
  {{- if .Values.persistence.models.annotations }}
  annotations:
    {{- .Values.persistence.models.annotations | toYaml | nindent 4 }}
  {{- end }}
spec:
  accessModes:
    - {{ .Values.persistence.models.accessMode }}
  resources:
    requests:
      storage: {{ .Values.persistence.models.size }}
  {{- if .Values.persistence.models.storageClass }}
  storageClassName: {{ .Values.persistence.models.storageClass }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Persistent volume claim for outputs
*/}}
{{- define "comfyui-engine.outputsPVC" -}}
{{- if .Values.persistence.outputs.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-outputs
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
  {{- if .Values.persistence.outputs.annotations }}
  annotations:
    {{- .Values.persistence.outputs.annotations | toYaml | nindent 4 }}
  {{- end }}
spec:
  accessModes:
    - {{ .Values.persistence.outputs.accessMode }}
  resources:
    requests:
      storage: {{ .Values.persistence.outputs.size }}
  {{- if .Values.persistence.outputs.storageClass }}
  storageClassName: {{ .Values.persistence.outputs.storageClass }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Persistent volume claim for cache
*/}}
{{- define "comfyui-engine.cachePVC" -}}
{{- if .Values.persistence.cache.enabled }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-cache
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
  {{- if .Values.persistence.cache.annotations }}
  annotations:
    {{- .Values.persistence.cache.annotations | toYaml | nindent 4 }}
  {{- end }}
spec:
  accessModes:
    - {{ .Values.persistence.cache.accessMode }}
  resources:
    requests:
      storage: {{ .Values.persistence.cache.size }}
  {{- if .Values.persistence.cache.storageClass }}
  storageClassName: {{ .Values.persistence.cache.storageClass }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Config map for application configuration
*/}}
{{- define "comfyui-engine.configMap" -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-config
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
data:
  config.yaml: |
    api:
      host: {{ .Values.config.api.host | quote }}
      port: {{ .Values.config.api.port }}
      workers: {{ .Values.config.api.workers }}
      timeout: {{ .Values.config.api.timeout }}
      max_request_size: {{ .Values.config.api.max_request_size }}
    
    comfyui:
      host: {{ .Values.config.comfyui.host | quote }}
      port: {{ .Values.config.comfyui.port }}
      path: {{ .Values.config.comfyui.path | quote }}
      timeout: {{ .Values.config.comfyui.timeout }}
    
    redis:
      enabled: {{ .Values.config.redis.enabled }}
      url: {{ include "comfyui-engine.redisUrl" . | quote }}
      pool_size: {{ .Values.config.redis.pool_size }}
    
    database:
      enabled: {{ .Values.config.database.enabled }}
      type: {{ .Values.config.database.type | quote }}
      host: {{ include "comfyui-engine.dbHost" . | quote }}
      port: {{ .Values.config.database.port }}
      name: {{ .Values.config.database.name | quote }}
      user: {{ .Values.config.database.user | quote }}
      pool_size: {{ .Values.config.database.pool_size }}
    
    model_cache:
      max_memory_mb: {{ .Values.config.model_cache.max_memory_mb }}
      max_models: {{ .Values.config.model_cache.max_models }}
      warmup_on_start: {{ .Values.config.model_cache.warmup_on_start }}
      eviction_policy: {{ .Values.config.model_cache.eviction_policy | quote }}
    
    auto_scaler:
      enabled: {{ .Values.config.auto_scaler.enabled }}
      min_workers: {{ .Values.config.auto_scaler.min_workers }}
      max_workers: {{ .Values.config.auto_scaler.max_workers }}
      scale_up_threshold: {{ .Values.config.auto_scaler.scale_up_threshold }}
      scale_down_threshold: {{ .Values.config.auto_scaler.scale_down_threshold }}
      scale_up_cooldown: {{ .Values.config.auto_scaler.scale_up_cooldown }}
      scale_down_cooldown: {{ .Values.config.auto_scaler.scale_down_cooldown }}
    
    security:
      jwt_algorithm: {{ .Values.config.security.jwt_algorithm | quote }}
      jwt_expiry: {{ .Values.config.security.jwt_expiry }}
      rate_limit: {{ .Values.config.security.rate_limit }}
      rate_limit_window: {{ .Values.config.security.rate_limit_window }}
    
    logging:
      level: {{ .Values.config.logging.level | quote }}
      format: {{ .Values.config.logging.format | quote }}
      output: {{ .Values.config.logging.output | quote }}
{{- end }}

{{/*
Secret for JWT
*/}}
{{- define "comfyui-engine.jwtSecret" -}}
{{- if and .Values.secrets.jwt.enabled (not .Values.secrets.jwt.existingSecret) }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "comfyui-engine.jwtSecretName" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
type: Opaque
data:
  jwt-secret: {{ randAlphaNum 32 | b64enc }}
{{- end }}
{{- end }}

{{/*
Secret for database
*/}}
{{- define "comfyui-engine.dbSecret" -}}
{{- if and .Values.secrets.db.enabled (not .Values.secrets.db.existingSecret) }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "comfyui-engine.dbSecretName" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
type: Opaque
data:
  password: {{ randAlphaNum 16 | b64enc }}
{{- end }}
{{- end }}

{{/*
Service monitor for Prometheus
*/}}
{{- define "comfyui-engine.serviceMonitor" -}}
{{- if and .Values.monitoring.enabled .Values.monitoring.serviceMonitor.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
    {{- if .Values.monitoring.serviceMonitor.labels }}
    {{- .Values.monitoring.serviceMonitor.labels | toYaml | nindent 4 }}
    {{- end }}
spec:
  selector:
    matchLabels:
      {{- include "comfyui-engine.selectorLabels" . | nindent 6 }}
  endpoints:
    - port: http
      path: /metrics
      interval: {{ .Values.monitoring.serviceMonitor.interval }}
      scrapeTimeout: {{ .Values.monitoring.serviceMonitor.scrapeTimeout }}
{{- end }}
{{- end }}

{{/*
Prometheus rule for alerts
*/}}
{{- define "comfyui-engine.prometheusRule" -}}
{{- if and .Values.monitoring.enabled .Values.monitoring.prometheusRule.enabled }}
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
    {{- if .Values.monitoring.prometheusRule.labels }}
    {{- .Values.monitoring.prometheusRule.labels | toYaml | nindent 4 }}
    {{- end }}
spec:
  groups:
    - name: {{ include "comfyui-engine.name" . }}
      rules:
        {{- .Values.monitoring.prometheusRule.rules | toYaml | nindent 8 }}
{{- end }}
{{- end }}

{{/*
Pod disruption budget
*/}}
{{- define "comfyui-engine.pdb" -}}
{{- if .Values.pdb.enabled }}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
spec:
  {{- if .Values.pdb.minAvailable }}
  minAvailable: {{ .Values.pdb.minAvailable }}
  {{- end }}
  {{- if .Values.pdb.maxUnavailable }}
  maxUnavailable: {{ .Values.pdb.maxUnavailable }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "comfyui-engine.selectorLabels" . | nindent 6 }}
{{- end }}
{{- end }}

{{/*
Network policy
*/}}
{{- define "comfyui-engine.networkPolicy" -}}
{{- if .Values.networkPolicy.enabled }}
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
spec:
  podSelector:
    matchLabels:
      {{- include "comfyui-engine.selectorLabels" . | nindent 6 }}
  policyTypes:
    - Ingress
    - Egress
  {{- if .Values.networkPolicy.ingress }}
  ingress:
    {{- .Values.networkPolicy.ingress | toYaml | nindent 4 }}
  {{- end }}
  {{- if .Values.networkPolicy.egress }}
  egress:
    {{- .Values.networkPolicy.egress | toYaml | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Horizontal pod autoscaler
*/}}
{{- define "comfyui-engine.hpa" -}}
{{- if .Values.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "comfyui-engine.fullname" . }}
  minReplicas: {{ .Values.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .Values.autoscaling.targetMemoryUtilizationPercentage }}
    {{- if .Values.autoscaling.metrics }}
    {{- .Values.autoscaling.metrics | toYaml | nindent 4 }}
    {{- end }}
  {{- if .Values.autoscaling.behavior }}
  behavior:
    {{- .Values.autoscaling.behavior | toYaml | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Vertical pod autoscaler
*/}}
{{- define "comfyui-engine.vpa" -}}
{{- if .Values.vpa.enabled }}
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: {{ include "comfyui-engine.fullname" . }}
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "comfyui-engine.fullname" . }}
  updatePolicy:
    updateMode: {{ .Values.vpa.updateMode | quote }}
  {{- if .Values.vpa.resourcePolicy }}
  resourcePolicy:
    {{- .Values.vpa.resourcePolicy | toYaml | nindent 4 }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Backup cron job
*/}}
{{- define "comfyui-engine.backupCronJob" -}}
{{- if .Values.backup.enabled }}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-backup
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
spec:
  schedule: {{ .Values.backup.schedule | quote }}
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: busybox:latest
              command:
                - sh
                - -c
                - |
                  echo "Running backup..."
                  # Add backup logic here
                  date
              resources:
                limits:
                  cpu: 500m
                  memory: 512Mi
                requests:
                  cpu: 100m
                  memory: 128Mi
          restartPolicy: OnFailure
{{- end }}
{{- end }}

{{/*
Grafana dashboard config map
*/}}
{{- define "comfyui-engine.grafanaDashboard" -}}
{{- if .Values.dashboards.enabled }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "comfyui-engine.fullname" . }}-dashboard
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
    {{- if .Values.dashboards.label }}
    {{ .Values.dashboards.label }}: {{ .Values.dashboards.labelValue | quote }}
    {{- end }}
data:
  comfyui-engine-dashboard.json: |
    {
      "dashboard": {
        "title": "ComfyUI Engine Dashboard",
        "panels": [
          {
            "title": "Request Rate",
            "type": "graph",
            "targets": [
              {
                "expr": "rate(comfyui_engine_requests_total[5m])"
              }
            ]
          },
          {
            "title": "Latency",
            "type": "graph",
            "targets": [
              {
                "expr": "histogram_quantile(0.95, rate(comfyui_engine_request_duration_seconds_bucket[5m]))"
              }
            ]
          },
          {
            "title": "GPU Utilization",
            "type": "graph",
            "targets": [
              {
                "expr": "nvidia_gpu_utilization_gpu"
              }
            ]
          },
          {
            "title": "Queue Depth",
            "type": "graph",
            "targets": [
              {
                "expr": "comfyui_engine_queue_depth"
              }
            ]
          }
        ]
      }
    }
{{- end }}
{{- end }}

{{/*
Test connection pod
*/}}
{{- define "comfyui-engine.testConnection" -}}
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "comfyui-engine.fullname" . }}-test-connection"
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
spec:
  containers:
    - name: wget
      image: busybox
      command: ['wget']
      args: ['{{ include "comfyui-engine.fullname" . }}:{{ .Values.service.port }}/health']
  restartPolicy: Never
{{- end }}

{{/*
Test metrics endpoint
*/}}
{{- define "comfyui-engine.testMetrics" -}}
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "comfyui-engine.fullname" . }}-test-metrics"
  labels:
    {{- include "comfyui-engine.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
spec:
  containers:
    - name: wget
      image: busybox
      command: ['wget']
      args: ['{{ include "comfyui-engine.fullname" . }}:{{ .Values.service.port }}/metrics']
  restartPolicy: Never
{{- end }}
