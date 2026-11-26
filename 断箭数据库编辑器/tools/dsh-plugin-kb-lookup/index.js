/**
 * dsh-kb-lookup —— 每轮把「知识库强命中」注入上下文 ＋ 一条静态路由行。
 *
 * ⛔ 这是**薄壳**：判定逻辑一行都不许写在这里，全部在
 *    BA_Mod_Maker_2.0/tools/kb_lookup.py --inject（仓内，有用例守着）。
 *    本文件只做三件事：拿本轮用户原话 -> 跑那条命令 -> 把 stdout 当消息追加。
 *
 * 两条互补的路（缺一条都会留下"沉默是二义的"那个洞）：
 *   · **推送**：agent/pre-step 每轮把强命中注入（有条件的，只推够格的）；
 *   · **路标**：systemPrompt 里一行静态规则 —— 告诉 Agent 注入了什么、以及
 *     「没看到注入」**不等于**「知识库里没有」，拿不准就自己去查。
 *
 * 三条硬约束（每条都是 DSH 的真实行为逼出来的，别改）：
 *   ① agent/pre-step **每一步**都触发（含工具步）⇒ 必须按 turn 去重，否则重复注入；
 *   ② 注入是**已记账消息**，会永久写进会话日志、每步都吃一遍 token
 *      ⇒ 只在命令有输出时才注入；命令 0 命中时输出空串，这里就什么都不加；
 *   ③ 外部命令必须限时 + 限长，否则会拖住每一步。
 */
import { execFile } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { createUserMessage } from '@deepseek-ai/dsh-llm';

export const name = 'kb-lookup';

//: 与 openviking 插件同一份服务清单 —— 那是在跑的样板，别自己发明。
export const inject = ['agents', 'sessions', 'tools', 'systemPrompt'];

export function apply(ctx, config = {}) {
  const python = config.python;
  const script = config.script;
  //: 仓库根 = `<仓>/tools/kb_lookup.py` 的上两级。⛔ 静态路标里不许再写死仓目录名 ——
  //: 用户 2026-10-30 把它改名成 `BA_Mod_Maker_2.0（数据侧）`，写死的路径会把下个会话指到不存在的目录。
  const repoRoot = config.repoRoot || (script ? dirname(dirname(String(script))) : '');
  const minLen = config.minQueryLength === undefined ? 6 : config.minQueryLength;
  const timeoutMs = config.timeoutMs === undefined ? 8000 : config.timeoutMs;
  const maxChars = config.maxChars === undefined ? 1200 : config.maxChars;
  if (!python || !script) {
    ctx.logger.warn('[kb-lookup] 没配 python / script，插件不做事');
    return;
  }

  //: ★ 心跳：**每次挂载**就落一个文件 —— 把"宿主到底加载没加载我"从猜变成一条读数。
  //: 动机（2026-10-11）：有一次重启后系统提示里**看不到路标行**，而我**没有任何凭证**
  //: 判断是"插件没加载"还是"渲染没显示"。静默失败必须留痕 —— 这是 Hindsight 的教训。
  //: 写的是 _work/ 下的一个 json（不是日志），--check 会读它。
  const heartbeat = config.heartbeat;
  const beat = extra => {
    if (!heartbeat) return;
    try {
      mkdirSync(dirname(heartbeat), { recursive: true });
      let cur = {};
      try {
        cur = JSON.parse(readFileSync(heartbeat, 'utf8'));
      } catch {
        cur = {};
      }
      writeFileSync(heartbeat, JSON.stringify({ ...cur, ...extra }, null, 1), 'utf8');
    } catch {
      //: 心跳自己不许把插件弄挂。
    }
  };
  beat({ mountAt: new Date().toISOString(), pid: process.pid, inject: inject });

  //: 已经注入过的 turn —— agent/pre-step 每步都触发，没有它就会重复注入。
  const done = new Set();

  //: ★ 会话内**按内容**去重：同一段注入不许重复出现。
  //: 依据（2026-10-11 用户实测「注入了但会话没读」）：原来每轮都往会话里追加一块，
  //: 几轮之后满屏都是 🔎 ⇒ 模型学会把它当噪声忽略。**只在新内容出现时才注入**，
  //: 每一次出现就都是"新的"，才有人看。
  const seenText = new Set();

  const runOnce = (query, signal) => new Promise(resolve => {
    execFile(
      python,
      [script, '--inject', query],
      { timeout: timeoutMs, maxBuffer: 1 << 20, windowsHide: true, signal },
      (err, stdout) => resolve(err ? '' : String(stdout === undefined ? '' : stdout).trim()),
    );
  });

  //: ★ 成功率闸（2026-10-11）：用户原话「**你能不能让 Agent 自己会用，而不是有这个功能但不用**」。
  //: 判定**一行都不在这里** —— 全在仓内 tools/success_guard.py（有 --selftest 守着）。
  //: 这里只负责「每轮问它一句」：**有信号才注入**（没信号输出空串 ⇒ 一个 token 都不占）。
  //: 与 kb 那条同样的三条硬约束：按 turn 去重、只在有输出时注入、限时限长。
  const guard = config.guard;
  const runGuard = signal => new Promise(resolve => {
    if (!guard) {
      resolve('');
      return;
    }
    execFile(
      python,
      [guard, '--inject'],
      { timeout: timeoutMs, maxBuffer: 1 << 20, windowsHide: true, signal },
      (err, stdout) => resolve(err ? '' : String(stdout === undefined ? '' : stdout).trim()),
    );
  });

  // ── 路标：一行静态规则，零 token 增量、不占对话历史 ──────────────────────
  //: order 850 = 策略区（PLAN_POLICY 500 / TEAM_POLICY 600 / PTC_ONLY 800 之后，
  //: 工具说明 1000+ 之前）。interpolate 显式关掉：这段文本里有 Windows 反斜杠路径。
  ctx.systemPrompt.section({
    name: 'kb-lookup-route',
    order: 850,
    interpolate: false,
    text: [
      '## 知识库（本工作区）—— 先查再猜，且"没看到注入"不等于"没有"',
      '',
      '碰任何「这个项目怎么做 / 这个字段在哪 / 该拿哪把尺子」之前，先跑这一条：',
      '',
      `    cd ${repoRoot}`,
      '    .venv\\Scripts\\python.exe tools\\kb_lookup.py "<就用你平时怎么说的>"',
      '',
      '· 对话里出现「⚠️ 知识库命中（自动注入…）」那一块 = **自动注入**，**先看它再动手**；',
      '  它**内联了正文**（不用再去读文件），并要求你在**回复第一行**写回执：',
      '  「kb: 已用 <文件>」或「kb: 不相关 —— <为什么>」。**这一行是给用户看的。**',
      '· 它**不出现**，只表示"这一轮没有够格自动推送的强命中"——',
      '  ⛔ **绝不表示知识库里没有**。拿不准就自己跑上面那条命令。',
      '· 0 命中也是一种答案，而且它会告诉你哪些词**全库 0 次出现**（那才是"大概没写过"的证据）。',
      '· 改检索口径 ⇒ 改 tools/kb_lookup.py（别在别处再写一份），口径见 kb/29。',
      '',
      '## 🚦 成功率闸（同一插件、同一时刻推送）',
      '',
      '· 出现「🚦 成功率闸（自动）：检测到一局新日志没进台账」= **真的有一局没读**（不是唠叨，',
      '  有信号才出现）。照它给的两条命令做完：先 game_log.py --new-only 看，再 record 记账。',
      '· **没出现** = 目前没有未记账的局 —— 不代表不用记：**做成的也要记**（否则账本只剩事故）。',
      '· 口径、四指标、晋升判据见 kb/34；工具 tools/game_log.py · tools/acceptance_log.py。',
      '· 改判定 ⇒ 改 tools/success_guard.py（别改插件），跑它的 --selftest。',
    ].join('\n'),
  });

  ctx.on('agent/pre-step', async (input, next) => {
    const decision = await next();
    if (!decision || decision.kind !== 'enter') return decision;
    if (input.signal && input.signal.aborted) return decision;
    const messages = input.messages;
    //: 只有"本轮真有用户输入"的那一步才跑 —— 工具步的 messages 是空的。
    if (!messages || messages.length === 0) return decision;

    const turn = input.turn;
    if (turn !== undefined) {
      if (done.has(turn)) return decision;
      done.add(turn);
      if (done.size > 128) done.clear();
    }

    const query = messages
      .flatMap(m => (m && m.content) || [])
      .filter(b => b && b.type === 'text')
      .map(b => b.text)
      .join('\n')
      .trim();
    if (query.length < minLen) return decision;

    const parts = [];
    const kbText = await runOnce(query, input.signal);
    if (kbText) parts.push(kbText);
    const guardText = await runGuard(input.signal);
    if (guardText) parts.push(guardText);
    if (parts.length === 0) return decision;
    let text = parts.join('\n\n');
    if (input.signal && input.signal.aborted) return decision;
    if (text.length > maxChars) text = text.slice(0, maxChars);
    if (seenText.has(text)) return decision;
    seenText.add(text);

    //: 每次注入都落一行日志 —— 「到底注入了没有」必须可查，别靠猜。
    ctx.logger.info(
      '[kb-lookup] 注入 ' + text.length + ' 字符：' + text.split('\n')[1],
    );
    beat({ lastInjectAt: new Date().toISOString(), lastInjectChars: text.length });
    const injected = createUserMessage({
      content: [{ type: 'text', text }],
      source: { kind: 'plugin:kb-lookup', plugin: 'kb-lookup' },
    });
    return { kind: 'enter', messages: [...decision.messages, injected] };
  }, { prepend: true });
}
/**
 * 维护口径：
 *   · 改判定 ⇒ 改 tools/kb_lookup.py（别改这里），跑 --selftest 与 pytest tests/test_kb_lookup.py；
 *   · 装/卸/查 ⇒ 仓库 tools/install_kb_plugin.py（--check / --uninstall）；
 *   · 本文件是**源**，真正的包在 ~/.dsh/profiles/desktop/node_modules/dsh-kb-lookup/。
 */