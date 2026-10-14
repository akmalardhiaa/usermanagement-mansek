"""Audit every bundled Maven library in the built service JAR against OSV.

Run after ``mvn package``. Unknown library coordinates fail closed so a new
dependency cannot silently escape the audit.
"""

import io
import json
import re
import sys
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent / "target" / "ad-service.jar"
packages = []
unknown = []

with zipfile.ZipFile(source) as outer:
    for path in outer.namelist():
        if not path.startswith("BOOT-INF/lib/") or not path.endswith(".jar"):
            continue
        with zipfile.ZipFile(io.BytesIO(outer.read(path))) as jar:
            props = [name for name in jar.namelist() if name.startswith("META-INF/maven/") and name.endswith("/pom.properties")]
            if len(props) != 1:
                filename = path.rsplit("/", 1)[-1]
                match = re.fullmatch(r"(.+)-(\d+\.\d+[^/]*)\.jar", filename)
                if not match:
                    unknown.append(path)
                    continue
                artifact, version = match.groups()
                if artifact.startswith("spring-boot"):
                    group = "org.springframework.boot"
                elif artifact.startswith("spring-"):
                    group = "org.springframework"
                elif artifact.startswith("tomcat-embed"):
                    group = "org.apache.tomcat.embed"
                elif artifact.startswith("micrometer-"):
                    group = "io.micrometer"
                elif artifact == "unboundid-ldapsdk":
                    group = "com.unboundid"
                elif artifact == "jspecify":
                    group = "org.jspecify"
                else:
                    unknown.append(path)
                    continue
                packages.append({"name": group + ":" + artifact, "version": version})
                continue
            values = {}
            for line in jar.read(props[0]).decode("utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    values[key] = value
            if not all(key in values for key in ("groupId", "artifactId", "version")):
                unknown.append(path)
                continue
            packages.append({"name": values["groupId"] + ":" + values["artifactId"], "version": values["version"]})

queries = [{"package": {"ecosystem": "Maven", "name": package["name"]}, "version": package["version"]} for package in packages]
request = urllib.request.Request("https://api.osv.dev/v1/querybatch", data=json.dumps({"queries": queries}).encode(), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=120) as response:
    results = json.load(response)["results"]

findings = [{**package, "vulns": result.get("vulns", [])} for package, result in zip(packages, results) if result.get("vulns")]
ids = sorted({vuln["id"] for finding in findings for vuln in finding["vulns"]})
def detail(vuln_id):
    with urllib.request.urlopen("https://api.osv.dev/v1/vulns/" + vuln_id, timeout=60) as response:
        item = json.load(response)
    return vuln_id, {"published": item.get("published"), "summary": item.get("summary"), "affected": item.get("affected")}
with ThreadPoolExecutor(max_workers=6) as pool:
    details = dict(pool.map(detail, ids))
report = {"jar": str(source), "packages_checked": len(packages), "unidentified_jars": unknown, "findings": findings, "advisories": details}
destination = source.parent / "security-audit.json"
destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
print("packages_checked:", len(packages))
print("unidentified_jars:", unknown)
for finding in findings:
    print(finding["name"], finding["version"])
    for vuln in finding["vulns"]:
        advisory = details[vuln["id"]]
        fixes = sorted({event["fixed"] for affected in advisory["affected"] for version_range in affected.get("ranges", []) for event in version_range.get("events", []) if "fixed" in event})
        print(" ", vuln["id"], advisory["published"], "fixed:", fixes, advisory["summary"])
if unknown or findings:
    raise SystemExit(1)
