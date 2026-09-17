set -eu
echo "== 1. 全新 DinD（独立 daemon + 独立卷） =="
docker rm -f rg-clean-dind >/dev/null 2>&1 || true
docker volume rm rg-clean-dind-data >/dev/null 2>&1 || true
docker network rm rg-clean-net >/dev/null 2>&1 || true
docker network create rg-clean-net >/dev/null
docker volume create rg-clean-dind-data >/dev/null
docker run -d --name rg-clean-dind --privileged --network rg-clean-net \
  -e DOCKER_TLS_CERTDIR= \
  -v rg-clean-dind-data:/var/lib/docker \
  docker.m.daocloud.io/library/docker:27-dind --registry-mirror=https://docker.m.daocloud.io >/dev/null
for i in $(seq 1 60); do docker exec rg-clean-dind docker info >/dev/null 2>&1 && break; sleep 2; done
docker exec rg-clean-dind docker info --format 'daemon={{.ServerVersion}} mirrors={{.RegistryConfig.Mirrors}}'
echo "== 2. 容器内依赖 =="
docker exec rg-clean-dind apk add --no-cache bash python3 curl openssl util-linux coreutils >/dev/null 2>&1
docker exec rg-clean-dind sh -c 'command -v flock python3 curl openssl docker; docker compose version | head -1'
echo "== 3. 干净工作区副本 =="
docker exec rg-clean-dind rm -rf /work >/dev/null 2>&1 || true
docker cp /root/rgops/gate-head/revguard rg-clean-dind:/work
docker exec rg-clean-dind sh -c 'cd /work && git status 2>/dev/null | head -1; ls | head -6'
echo "== 4. 从零部署（--local --observability，空数据库） =="
docker exec rg-clean-dind sh -c 'cd /work && bash scripts/deploy_demo.sh --local --observability' 2>&1 | tail -40
echo "CLEAN_DEPLOY_DONE"
