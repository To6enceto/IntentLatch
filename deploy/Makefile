SHELL := /bin/bash
.DEFAULT_GOAL := help
PROFILE ?= cloud
KUBE_CONTEXT ?= $(if $(filter local,$(PROFILE)),k3d-intentlatch,do-fra1-hackyeah-cluster)
OLLAMA_NODE ?= pool-d5e2gdbur-3x1f37
IMAGE_TAG ?= $(shell git -C .. rev-parse HEAD)
IMAGE := ghcr.io/antonstwork/intentlatch-gateway:$(IMAGE_TAG)
export PROFILE KUBE_CONTEXT OLLAMA_NODE IMAGE_TAG
export PATH := $(CURDIR)/.state/bin:$(PATH)

.PHONY: help tools cluster-prep install models smoke bootstrap bootstrap-webui access status destroy build publish check-image namespaces sealed-secrets secrets postgres valkey ollama gateway webui monitoring edge local-cluster image-import validate
help:
	@echo 'make build publish; make install; make bootstrap; make smoke; make status; make destroy'
	@echo 'Staged: cluster-prep sealed-secrets namespaces secrets postgres valkey ollama models gateway bootstrap webui monitoring'
	@echo 'Later: make edge with site-values.yaml; local: PROFILE=local local-cluster image-import install'

tools cluster-prep install models status destroy check-image namespaces sealed-secrets secrets postgres valkey ollama gateway webui monitoring edge local-cluster:
	python3 scripts/manage.py $@

build:
	docker build --platform linux/amd64 -f docker/gateway.Dockerfile -t $(IMAGE) ..

publish:
	docker push $(IMAGE)
	@echo 'Set package visibility to Public in GHCR, then run make check-image.'

image-import:
	k3d image import $(IMAGE) -c intentlatch

bootstrap:
	python3 scripts/smoke.py --bootstrap-only

bootstrap-webui:
	python3 scripts/bootstrap-webui.py

access:
	python3 scripts/access.py

smoke:
	python3 scripts/smoke.py $(SMOKE_ARGS)

validate:
	python3 scripts/validate.py
