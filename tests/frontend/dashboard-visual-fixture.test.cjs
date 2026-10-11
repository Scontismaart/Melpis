const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {reply,readFixtures}=require("./fixtures/dashboard-visual-api.cjs");
const {buildQaBootstrap}=require("../../scripts/dashboard-baseline-server.cjs");
const root=path.resolve(__dirname,"../..");
test("visual fixtures fail closed on unknown reads and all unapproved writes",()=>{
  for(const endpoint of Object.keys(readFixtures)) {
    assert.equal(reply("GET",endpoint).status,200);
    for(const method of ["POST","PATCH","DELETE","PUT"]) assert.equal(reply(method,endpoint).status,501);
  }
  assert.equal(reply("GET","/api/unexpected").status,501);
  assert.equal(reply("POST","/api/messaggio").body.richiede_umano,false);
});
test("synthetic identity and fixtures contain no provider credentials or real accounts",()=>{
  assert.match(readFixtures["/api/auth/me"].email,/@dashboard-qa\.invalid$/);
  assert.doesNotMatch(JSON.stringify(readFixtures),/access_token|refresh_token|service_role|app_secret|password|qr_code|totp_secret/i);
});
test("QA bootstrap and synthetic server cannot ship in the dashboard Docker image",()=>{
  const html=fs.readFileSync(path.join(root,"web/index.html"),"utf8");
  const docker=fs.readFileSync(path.join(root,"web/Dockerfile"),"utf8");
  assert.doesNotMatch(html,/__qa|dashboard-baseline|dashboard-visual-api/);
  assert.doesNotMatch(docker,/dashboard-baseline|dashboard-visual-api|COPY\s+tests/);
});
test("QA capture metadata records reproducible environment without reading credentials",()=>{
  const script=buildQaBootstrap("dashboard_qa_case=ai-settings-populated; dashboard_qa_signed_out=1");
  assert.match(script,/\"scenario\":\"ai-settings-populated\"/);
  assert.match(script,/navigator\.userAgent/);
  assert.match(script,/devicePixelRatio/);
  assert.match(script,/visualViewport\?\.scale/);
  assert.match(script,/document\.fonts\.ready/);
  assert.match(script,/loadedFamilies/);
  assert.match(script,/timeZone/);
  assert.match(script,/fixedClock/);
  assert.match(script,/data-qa-environment|qaEnvironment/);
  assert.doesNotMatch(script,/localStorage\.getItem\(['\"](?:access_token|refresh_token|auth_token|session_token)['\"]\)/i);
  assert.doesNotMatch(buildQaBootstrap("dashboard_qa_case=untrusted-value"),/\"scenario\":\"untrusted-value\"/);
});
