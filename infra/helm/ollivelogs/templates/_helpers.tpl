{{/*
Shared template helpers.
*/}}

{{- define "ollive.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ollive.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "ollive.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ollive.componentLabels" -}}
{{ include "ollive.labels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "ollive.componentSelector" -}}
{{ include "ollive.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "ollive.image" -}}
{{ .Values.global.imageRegistry }}/{{ .image }}:{{ .Values.global.imageTag }}
{{- end -}}

{{/* DSNs/URLs that several services share. */}}
{{- define "ollive.databaseUrl" -}}
postgresql+asyncpg://ollive:ollive@{{ .Release.Name }}-postgres.{{ .Values.global.namespace }}.svc.cluster.local:{{ .Values.postgres.port }}/ollivelogs
{{- end -}}

{{- define "ollive.redisUrl" -}}
redis://{{ .Release.Name }}-redis.{{ .Values.global.namespace }}.svc.cluster.local:{{ .Values.redis.port }}/0
{{- end -}}

{{- define "ollive.clickhouseHost" -}}
{{ .Release.Name }}-clickhouse.{{ .Values.global.namespace }}.svc.cluster.local
{{- end -}}

{{- define "ollive.otlpEndpoint" -}}
http://{{ .Release.Name }}-otel-collector.{{ .Values.global.namespace }}.svc.cluster.local:{{ .Values.otelCollector.grpcPort }}
{{- end -}}
