{{- define "intentlatch.labels" -}}
app.kubernetes.io/part-of: intentlatch
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "intentlatch.security" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "intentlatch.generalAffinity" -}}
{{- if .Values.ollama.dedicatedNode }}
nodeAffinity:
  requiredDuringSchedulingIgnoredDuringExecution:
    nodeSelectorTerms:
      - matchExpressions:
          - key: intentlatch.io/role
            operator: NotIn
            values: [ollama]
{{- else }}
{}
{{- end }}
{{- end }}
