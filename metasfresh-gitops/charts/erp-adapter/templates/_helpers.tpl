{{- define "erp.namespace" -}}
{{- default .Release.Namespace .Values.global.namespace -}}
{{- end -}}

{{- define "erp.labels" -}}
app.kubernetes.io/part-of: metasfresh
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "erp.serviceAccount" -}}
{{- if .Values.serviceAccount.create -}}
{{- default "erp-app" .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "erp.claimName" -}}
{{- $cfg := index .root.Values .component -}}
{{- default (printf "%s-business-pvc" .component) $cfg.persistence.existingClaim -}}
{{- end -}}

{{- define "erp.frontendConfig" -}}
const config = {
  API_URL: {{ .Values.ui.config.apiUrl | toJson }},
  WS_URL: {{ .Values.ui.config.websocketUrl | toJson }}
};
{{- end -}}

{{- define "erp.backendEnv" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $cfg := index $root.Values $component -}}
{{- $javaOptions := $cfg.javaOptions -}}
{{- if eq $component "api" -}}
{{- $javaOptions = printf "%s -Dmetasfresh.webui.email.attachmentsDir=%s/email-attachments" $javaOptions $cfg.persistence.mountPath -}}
{{- end -}}
{{- if $root.Values.databaseTLS.enabled -}}
{{- $javaOptions = printf "%s -Dmetasfresh.db.sslmode=%s -Dmetasfresh.db.sslrootcert=%s" $javaOptions $root.Values.databaseTLS.mode $root.Values.databaseTLS.mountPath -}}
{{- end -}}
- name: PropertyFile
  value: {{ if eq $component "core" }}"/opt/metasfresh/metasfresh.properties"{{ else }}"/opt/metasfresh-webui-api/metasfresh.properties"{{ end }}
- name: JAVA_TOOL_OPTIONS
  value: {{ $javaOptions | quote }}
- name: SPRING_RABBITMQ_HOST
  value: {{ $root.Values.externalDB.rabbitHost | quote }}
- name: SPRING_RABBITMQ_PORT
  value: {{ $root.Values.externalDB.rabbitPort | quote }}
- name: SPRING_RABBITMQ_USERNAME
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.runtime.rabbitmqSecret.name }}
      key: {{ $root.Values.runtime.rabbitmqSecret.usernameKey }}
- name: SPRING_RABBITMQ_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ $root.Values.runtime.rabbitmqSecret.name }}
      key: {{ $root.Values.runtime.rabbitmqSecret.passwordKey }}
- name: METASFRESH_ELASTICSEARCH_HOST
  value: {{ printf "%s:%v" $root.Values.externalDB.searchHost $root.Values.externalDB.searchPort | quote }}
- name: SPRING_DATA_ELASTICSEARCH_CLIENT_REACTIVE_ENDPOINTS
  value: {{ printf "%s:%v" $root.Values.externalDB.searchHost $root.Values.externalDB.searchPort | quote }}
{{- if gt (int $root.Values.externalDB.searchTransportPort) 0 }}
- name: SPRING_DATA_ELASTICSEARCH_CLUSTER_NODES
  value: {{ printf "%s:%v" $root.Values.externalDB.searchHost $root.Values.externalDB.searchTransportPort | quote }}
{{- end }}
{{- with $cfg.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "erp.deployment" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $cfg := index $root.Values $component -}}
{{- $name := printf "metasfresh-%s" $component -}}
{{- $backend := ne $component "ui" -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  namespace: {{ include "erp.namespace" $root }}
  labels:
    {{- include "erp.labels" $root | nindent 4 }}
  annotations:
    argocd.argoproj.io/sync-wave: {{ if eq $component "core" }}"20"{{ else if eq $component "api" }}"30"{{ else }}"40"{{ end }}
spec:
  {{- if or $backend (not $cfg.autoscaling.enabled) }}
  replicas: {{ $cfg.replicaCount }}
  {{- end }}
  strategy:
    {{- if $backend }}
    # Single writer with a RWO volume; avoid overlap and Multi-Attach on upgrades.
    type: Recreate
    {{- else }}
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
    {{- end }}
  selector:
    matchLabels:
      app: {{ $name }}
  template:
    metadata:
      labels:
        app: {{ $name }}
        {{- include "erp.labels" $root | nindent 8 }}
      annotations:
        {{- if $backend }}
        runtime-secret-revision: {{ $root.Values.runtime.propertiesSecret.revision | quote }}
        backup.velero.io/backup-volumes: business-data
        {{- else }}
        checksum/frontend-config: {{ include "erp.frontendConfig" $root | sha256sum }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "erp.serviceAccount" $root }}
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: 60
      {{- with $root.Values.global.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $cfg.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $cfg.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $cfg.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- if not $backend }}
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels:
              app: {{ $name }}
      {{- end }}
      containers:
        - name: {{ $component }}
          image: "{{ $cfg.image.repository }}{{ if $cfg.image.digest }}@{{ $cfg.image.digest }}{{ else }}:{{ $cfg.image.tag }}{{ end }}"
          imagePullPolicy: {{ $cfg.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .port }}
          {{- with $cfg.securityContext }}
          securityContext:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          resources:
            {{- toYaml $cfg.resources | nindent 12 }}
          {{- if $backend }}
          env:
            {{- include "erp.backendEnv" . | nindent 12 }}
          {{- else }}
          {{- with $cfg.extraEnv }}
          env:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- end }}
          {{- with $cfg.probes.startup }}
          startupProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with $cfg.probes.readiness }}
          readinessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with $cfg.probes.liveness }}
          livenessProbe:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          volumeMounts:
            {{- if $backend }}
            - name: erp-properties
              mountPath: {{ if eq $component "core" }}"/opt/metasfresh/metasfresh.properties"{{ else }}"/opt/metasfresh-webui-api/metasfresh.properties"{{ end }}
              subPath: metasfresh.properties
              readOnly: true
            - name: business-data
              mountPath: {{ $cfg.persistence.mountPath }}
            {{- if $root.Values.databaseTLS.enabled }}
            - name: postgres-ca
              mountPath: {{ $root.Values.databaseTLS.mountPath }}
              subPath: ca.crt
              readOnly: true
            {{- end }}
            {{- else }}
            - name: frontend-config
              mountPath: /usr/share/nginx/html/config.js
              subPath: config.js
              readOnly: true
            {{- end }}
      volumes:
        {{- if $backend }}
        - name: erp-properties
          secret:
            secretName: {{ $root.Values.runtime.propertiesSecret.name }}
            defaultMode: 0440
            items:
              - key: {{ $root.Values.runtime.propertiesSecret.key }}
                path: metasfresh.properties
        {{- if $root.Values.databaseTLS.enabled }}
        - name: postgres-ca
          secret:
            secretName: {{ $root.Values.databaseTLS.secretName }}
            defaultMode: 0444
            items:
              - key: {{ $root.Values.databaseTLS.key }}
                path: ca.crt
        {{- end }}
        - name: business-data
          {{- if $cfg.persistence.enabled }}
          persistentVolumeClaim:
            claimName: {{ include "erp.claimName" . }}
          {{- else }}
          emptyDir: {}
          {{- end }}
        {{- else }}
        - name: frontend-config
          configMap:
            name: metasfresh-frontend-config
        {{- end }}
{{- end -}}
