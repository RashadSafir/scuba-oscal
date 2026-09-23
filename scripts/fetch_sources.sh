#!/usr/bin/env bash
set -e
mkdir -p vendor
[ -d vendor/ScubaGear ] || git clone --depth 1 https://github.com/cisagov/ScubaGear.git vendor/ScubaGear
curl -sL -o vendor/NIST_SP-800-53_rev5_catalog.json https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json
