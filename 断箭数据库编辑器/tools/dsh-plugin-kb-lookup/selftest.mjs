/**
 * dsh-kb-lookup 的**插件自检** —— 用假的 ctx 把 apply() 真跑起来。
 *
 * 验的是"插件这一侧"：钩子注册 / 取原话 / 跑命令 / 造消息 / 按 turn 去重 / 空命中不注入。
 * ⛔ **判定逻辑不在这里**（那在仓内 tools/kb_lookup.py，由 pytest 的 28 条守着）；
 *    本文件只验薄壳有没有把命令喂对、把结果接对。
 *
 * 跑法（**必须在 profile 下跑**，那里才解析得到 @deepseek-ai/dsh-llm）：
 *     cd ~/.dsh/profiles/desktop
 *     node node_modules/dsh-kb-lookup/selftest.mjs
 *
 * 它读的是**真配置**：从 ../../cordis.patch.yml 里取 python / script，
 * 所以配置写错了这里就会红 —— 不是拿一组假路径自娱自乐。
 */
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const profile = dirname(dirname(here));
const patch = readFileSync(join(profile, 'cordis.patch.yml'), 'utf8');
const grab = key => {
  const m = patch.match(new RegExp(key + ":\\s*'([^']+)'"));
  return m ? m[1] : undefined;
};
const python = grab('python');
const script = grab('script');
const heartbeat = grab('heartbeat');

const checks = [];
const ok = (name, pass, detail) => checks.push({ name, pass, detail: detail || '' });

function makeCtx() {
  const handlers = [];
  const sections = [];
  return {
    handlers,
    sections,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    systemPrompt: { section: s => { sections.push(s); return () => {}; } },
    on: (event, handler, opts) => { handlers.push({ event, handler, opts }); },
  };
}

//: ⛔ 心跳用**临时路径**，不许用真配置那个 —— 自检写了真心跳，
//: --check 就会把"自检跑过"误报成"宿主加载过"。
const tmpBeat = join(mkdtempSync(join(tmpdir(), 'kb-lookup-beat-')), 'beat.json');

const { apply } = await import('./index.js');
const ctx = makeCtx();
apply(ctx, { python, script, heartbeat: tmpBeat });

ok('配置能从真 cordis.patch.yml 取到', Boolean(python && script), 'python=' + python + ' script=' + script);
ok('配置里有 heartbeat 路径', Boolean(heartbeat), heartbeat || '(cordis.patch.yml 里缺 heartbeat)');
//: ★ 挂载即落心跳 —— 这是"宿主到底加载没加载我"的唯一凭证（静默失败必须留痕）。
ok('挂载即落心跳文件', existsSync(tmpBeat), tmpBeat);
ok('注册了 agent/pre-step', ctx.handlers.some(h => h.event === 'agent/pre-step'));
const route = ctx.sections.find(s => s.name === 'kb-lookup-route');
ok('注册了路标段 kb-lookup-route', Boolean(route), route ? 'order=' + route.order : '');
ok('路标段关掉了插值', route ? route.interpolate === false : false);

const step = ctx.handlers.find(h => h.event === 'agent/pre-step');
const call = (text, turn) => {
  const messages = text === null ? [] : [{ content: [{ type: 'text', text }] }];
  return step.handler({ messages, turn, signal: undefined }, async () => ({
    kind: 'enter',
    messages: [...messages],
  }));
};
const injectedText = d => {
  const last = d.messages[d.messages.length - 1];
  return last && last.content && last.content[0] ? last.content[0].text : '';
};

const hit = await call('怎么造一个炮塔包', 101);
const hitText = injectedText(hit);
ok('有强命中 -> 多注入一条消息', hit.messages.length === 2, '条数=' + hit.messages.length);
ok('注入含 ⚠️ 抬头（点明不是用户发言）', hitText.includes('⚠️ 知识库命中'), hitText.split('\n')[0] || '');
ok('注入内容带 kb/24', hitText.includes('kb/24-造一个炮塔包'), '');
ok('注入块够短（<=900 字符）', hitText.length <= 900, hitText.length + ' 字符');
//: ★ 守着"内联正文、不许只给指针" —— 2026-10-11 用户实测「注入了但会话没读」的根因。
ok('注入内联了正文（不是指针）', hitText.length >= 200, hitText.length + ' 字符');
ok('注入要回执', hitText.includes('第一行') && hitText.includes('kb:'), '');

const again = await call('怎么造一个炮塔包', 101);
ok('同一 turn 第二次 -> 不再注入（去重）', again.messages.length === 1, '条数=' + again.messages.length);

//: ★ 空命中用**桩脚本**，不许拿"某句真话"当用例。
//: ⛔ 2026-10-11 第四次踩同一个坑：把「我重启了，你试试看吧」写进 kb/29 之后，
//:   引语桥把那句话认成了"记录过的问法"，它就不再是空命中 —— 这条用例当场变红。
//:   **用例只要依赖语料内容，文档一引用它就失效。** 桩是语料无关的。
const stubDir = mkdtempSync(join(tmpdir(), 'kb-lookup-stub-'));
const stubScript = join(stubDir, 'stub.py');
writeFileSync(stubScript, '', 'utf8');
const stubCtx = makeCtx();
apply(stubCtx, { python, script: stubScript });
const stubStep = stubCtx.handlers.find(h => h.event === 'agent/pre-step');
const stubMiss = await stubStep.handler(
  { messages: [{ content: [{ type: 'text', text: '怎么造一个炮塔包' }] }], turn: 201, signal: undefined },
  async () => ({ kind: 'enter', messages: [{ content: [{ type: 'text', text: 'x' }] }] }),
);
ok('空命中（桩）-> 什么都不加', stubMiss.messages.length === 1, '条数=' + stubMiss.messages.length);

const toolStep = await call(null, 103);
ok('没有用户消息（工具步）-> 不注入', toolStep.messages.length === 0, '条数=' + toolStep.messages.length);

//: ★ 成功率闸那条线（2026-10-11 加）—— 插件**真的会去问它**、并把结果并进**同一条**注入消息。
//: 判定逻辑全在仓内 tools/success_guard.py（自己的 --selftest：正控 3/3 · 负控 3/3）；
//: 这里只验薄壳「问没问、接没接」。夹具走 BA_GUARD_LOG_DIR / BA_GUARD_LEDGER 两个环境变量，
//: ⛔ **不碰真台账、真 GameLogs，也不拿任何真实语料当判据**（kb/29 §6 的教训）。
const guard = grab('guard');
ok('配置里有 guard 路径（成功率闸）', Boolean(guard), guard || '(cordis.patch.yml 里缺 guard)');

const guardCtx = makeCtx();
apply(guardCtx, { python, script: stubScript, guard, heartbeat: tmpBeat });
const guardStep = guardCtx.handlers.find(h => h.event === 'agent/pre-step');
//: ⚠️ 查询串必须 >= minQueryLength（默认 6），否则 handler 会**在跑任何命令之前**就返回 ——
//: 那样两条用例都会"绿"，但绿得没有意义（2026-10-11 本地就踩了一次：拿 'x' 当输入，
//: 负控假绿、正控假红）。这就是本仓 §0.9 说的「尺子必须先证明它能红」。
const GUARD_Q = 'fixture query for guard';
const callGuard = turn => guardStep.handler(
  { messages: [{ content: [{ type: 'text', text: GUARD_Q }] }], turn, signal: undefined },
  async () => ({ kind: 'enter', messages: [{ content: [{ type: 'text', text: GUARD_Q }] }] }),
);

//: 负控：夹具日志目录是空的 -> 一个字符都不许加
process.env.BA_GUARD_LOG_DIR = mkdtempSync(join(tmpdir(), 'guard-empty-'));
process.env.BA_GUARD_LEDGER = join(mkdtempSync(join(tmpdir(), 'guard-led-')), 'log.tsv');
//: ⛔ 心跳也必须走临时路径 —— 自检写了真心跳，--status 会把"自检跑过"当成"插件调用过"。
process.env.BA_GUARD_HEARTBEAT = join(mkdtempSync(join(tmpdir(), 'guard-beat-')), 'beat.json');
const quiet = await callGuard(301);
ok('闸·负控：没有未记账的局 -> 不加消息', quiet.messages.length === 1, '条数=' + quiet.messages.length);

//: 正控：造一局「没记账的日志」-> 必须并进注入里，且带 🚦 抬头
const guardLogs = mkdtempSync(join(tmpdir(), 'guard-logs-'));
writeFileSync(join(guardLogs, 'Gamelog__2026_10_11__23_59.log'),
  '[2026-10-11 23:59:00.000]\nError: fixture boom 12345\n', 'utf8');
process.env.BA_GUARD_LOG_DIR = guardLogs;
const fired = await callGuard(302);
const firedText = injectedText(fired);
ok('闸·正控：有未记账的局 -> 并进同一条消息', fired.messages.length === 2, '条数=' + fired.messages.length);
ok('闸·正控：注入块带 🚦 抬头', firedText.includes('🚦 成功率闸'), firedText.split('\n')[0] || '');
ok('闸·正控：给的命令能直接粘', firedText.includes('acceptance_log.py record'), '');
delete process.env.BA_GUARD_LOG_DIR;
delete process.env.BA_GUARD_LEDGER;
delete process.env.BA_GUARD_HEARTBEAT;


for (const c of checks) {
  console.log('  [' + (c.pass ? 'PASS' : 'FAIL') + '] ' + c.name + (c.detail ? ' —— ' + c.detail : ''));
}
const bad = checks.filter(c => !c.pass);
console.log('');
if (bad.length) {
  console.log('❌ 插件自检失败 ' + bad.length + ' 条');
  process.exit(1);
}
console.log('✅ 插件自检全绿（' + checks.length + ' 条）');