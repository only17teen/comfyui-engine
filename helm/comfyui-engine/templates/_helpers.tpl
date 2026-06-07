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
ConfigMap name
*/}}
{{- define "comfyui-engine.configMapName" -}}
{{- printf "%s-config" (include "comfyui-engine.fullname" .) }}
{{- end }}

{{/*
Secret name
*/}}
{{- define "comfyui-engine.secretName" -}}
{{- printf "%s-secrets" (include "comfyui-engine.fullname" .) }}
{{- end }}

{{/*
PVC name for output
*/}}
{{- define "comfyui-engine.outputPVCName" -}}
{{- printf "%s-output" (include "comfyui-engine.fullname" .) }}
{{- end }}

{{/*
PVC name for models
*/}}
{{- define "comfyui-engine.modelsPVCName" -}}
{{- printf "%s-models" (include "comfyui-engine.fullname" .) }}
{{- end }}
