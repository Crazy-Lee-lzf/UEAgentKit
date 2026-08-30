# UEAgentKit 椤圭洰鏁翠綋寮€鍙戣鍒?

> 鏃ユ湡锛?026-08-27
>
> 鏂囨。鎬ц川锛氶」鐩骇涓昏鍒掞紙Master Plan锛夛紝缁熻緰澶氭潯骞惰鍒嗘敮绾?
>
> 褰撳墠娲昏穬 worktree锛歚E:\WorkSpace\UEAgentKit-LiveWriter`锛堝垎鏀?`feature/live-writer-expansion`锛?
>
> 涓讳粨 worktree锛歚E:\WorkSpace\UEAgentKit`锛堝垎鏀?`feature/agent-reliability`锛?
>
> 鏈€鏂版寮忓彂甯冿細`0.7.0`锛圲E 5.6锛?
>
> 浼樺厛绾у喅绛栵紙鐢ㄦ埛纭锛夛細**W4 鍐欏叆鑳藉姏浼樺厛锛孧emory 澧炲己鎺掑叾鍚?*锛岄殢鍚?P4 鍗忎綔涓庣煡璇嗗簱 Web 鍙娴忚
>
> 鐭ヨ瘑搴撳啓鍏ョ害鏉燂紙鐢ㄦ埛纭锛夛細**鐭ヨ瘑搴撲笉鍏佽浜哄伐鐩存帴淇敼锛屽彧鑳界敱 Agent 鍐欏叆**

## 0. 鏈鍒掕瑙ｅ喅鐨勯棶棰?

0.8 capability scope 宸叉敹鍙ｏ紝`105 Tool / 18 Operation / 0 Must-fix`銆俉1鈥揥3 宸插湪鐪熷疄
UE5.6 涓婂畬鎴愬父椹?Writer 涓?Checkpoint Strong Verify銆傞」鐩凡缁?鑳借銆佽兘瀹夊叏鍐欍€?
鑳戒綆寤惰繜杩炵画鍐欍€佽兘寮洪獙璇?銆?

浣嗕笁浠朵簨浠嶇劧缂哄け锛屽苟涓斿畠浠喅瀹氫簡宸ュ叿鑳藉惁浠?鍗曠偣鍙敤"鍙樻垚"鏃ュ父鍙緷璧?锛?

```text
1. 澶氭搷浣?/ 澶氳祫浜т换鍔′粛闇€ Agent 鎵嬪伐閫愪釜 Plan/Apply/Save/Verify   鈫?W4
2. 椤圭洰璁板繂鍙湪 Agent 涓诲姩璋冪敤鏃跺啓鍏ワ紝涓嶄細鍦ㄤ娇鐢ㄤ腑鑷姩绉疮        鈫?Memory
3. 澶氫汉鍗忎綔娌℃湁 P4 鎰熺煡锛岀煡璇嗗簱娌℃湁浠讳綍鍙鍖栧叆鍙?               鈫?P4 / Web
```

鏈鍒掓妸杩欎笁浠朵簨鎷嗘垚鍥涙潯鍙苟琛屾帹杩涚殑 Track锛屽苟鏄庣‘鍚勮嚜鐨勫垎鏀€侀棬绂佷笌楠屾敹杈圭晫銆?

## 1. 浜嬪疄鍩虹嚎锛堝凡鍦ㄧ鐩樻牳瀹烇紝闈炴帹娴嬶級

### 1.1 Writer 绾跨姸鎬?

```text
W0 baseline / latency instrumentation      complete   142ca1e
W1 Blueprint narrow resident Live Apply    complete   8bede6f
W2 Fast Resident Verify                    complete   31f0faa
W3 Checkpoint Strong Verify                complete   C0-C6 鍏?PASS
W4 Multi-operation / Bounded Batch         璁″垝宸插啓锛屾湭瀹炵幇
```

W1 鏇捐鐪熷疄 UE5.6 鐨?Blueprint Undo crash 闃诲锛岃闂宸蹭慨澶嶏紱W3 宸插畬鎴愬苟浜?2026-08-27 褰㈡垚鐙珛 Git checkpoint锛?

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

W3 浜у搧浠ｇ爜銆佹祴璇曚笌缁撴灉鏂囨。宸茬粡浠?working tree 娓呭嚭锛孴0 鏀跺彛鏉′欢婊¤冻銆傚綋鍓嶆湭鎻愪氦鐨?README / Master / Midterm / W4 绛夋枃妗ｅ睘浜庡悗缁鍒掓暣鐞嗭紝涓嶅睘浜?W3 瀹炵幇杈圭晫銆?

### 1.2 Memory 绾跨姸鎬侊紙鍏抽敭缂哄彛鎵€鍦級

宸插疄鐜帮紙`memory_schema.py` v3锛?2 寮犺〃锛夛細

```text
memory_records / memory_scopes / memory_revisions / memory_artifacts
memory_relations / memory_status_events / memory_records_fts (FTS5)
knowledge_nodes / active_work_items / active_work_node_links
active_work_asset_links / active_work_todos
```

宸叉湁 14 涓?`ue_memory_*` Tool锛?

```text
add_rule  expand_node  export  get  get_context  get_evidence
mark_superseded  record_finding  record_task  search  status
update_knowledge  update_work  validate
```

**瀹炴祴缂哄彛**锛?

```text
鍚戦噺妫€绱?        闆跺疄鐜帮紙grep embedding/faiss/sqlite_vec 鏃犲懡涓級
                 pyproject dependencies = [] 锛屾棤浠讳綍绗笁鏂逛緷璧?
鑷姩璁板繂鎹曡幏     浠?memory_service.record_task_outcome 涓€涓叆鍙?
                 鍏朵綑鍏ㄩ儴渚濊禆 Agent 涓诲姩璋冪敤 ue_memory_record_*
绗﹀彿鍖栫煭鏈熻蹇?  鏃?
鍒嗗眰钂搁         鐭ヨ瘑鏍戞槸浜轰负 Path 灞傜骇锛屾病鏈?L0鈫扡3 鑷姩鎻愬崌绠＄嚎
鍥㈤槦鍏变韩         鏃?
```

缁撹锛?*褰撳墠璁板繂绯荤粺鏄?Agent 鑷鍐欍€丗TS5 鍏抽敭璇嶆煡"鐨勮鍔ㄥ簱**銆備綘瑕佺殑"杈圭敤杈圭Н绱?
鍦ㄥ綋鍓嶅疄鐜伴噷涓嶅瓨鍦紝杩欐槸 Memory Track 鐨勬牳蹇冨伐浣滈噺銆?

### 1.3 P4 涓?Web UI 鐘舵€?

```text
P4 / SourceControl    闆跺疄鐜帮紙浠?Automation handler 鍐呮湁鏃犲叧瀛楃涓插懡涓級
Web UI / HTTP Server  闆跺疄鐜帮紙鏃?fastapi / uvicorn / starlette / http.server锛?
```

### 1.4 浠ｇ爜瑙勬ā

```text
Python           35,885 琛?/ src/ue_agent_kit
  鏈€澶фā鍧?       agent_workflow.py 5,454 琛? 鈫?宸叉槸缁存姢鐑偣
                  patches.py 2,253 / task_context.py 1,731
                  mcp_server.py 1,549 / project_memory.py 1,174
C++ Plugin       ~14,000 琛岋紙EditorBridge 17 handler / LiveWrite / AssetReaders锛?
娴嬭瘯             85 涓枃浠讹紱褰撳墠 live-writer discovered suite 712锛?.8 closeout 鍘嗗彶 full suite 涓?739锛?
鏂囨。             146 涓?Markdown
```

## 2. 鍙傝€冩灦鏋勶細涓や釜宸查獙璇佺郴缁熺殑鍙€熼壌閮ㄥ垎

鏈鍒掑弬鑰冧袱涓凡楠岃瘉鐨?Agent 璁板繂绯荤粺锛屼竴涓拡瀵归€氱敤 Agent锛圱encentDB锛夛紝涓€涓拡瀵?
鏁板鎺ㄧ悊锛圖anus锛夈€備袱鑰呯殑璁捐楂樺害浜掕ˉ銆?

### 2.0 Danus锛欶act-Graph Memory 鍦ㄦ暟瀛︽帹鐞嗕腑鐨勯獙璇?

[Danus](https://arxiv.org/abs/2607.06447) 鏄?FrenzyMath 鍥㈤槦寮€婧愮殑鏁板璇佹槑 Agent
绯荤粺锛屾牳蹇冩灦鏋勬槸锛?

```text
Main agent (planning + coordination)
  鈫?Multiple worker agents (parallel proof search)
  鈫?Stateless verifier (Lean 4 formal proof checker)
  鈫?Fact-Graph Memory (verified lemmas admitted as nodes)
  鈫?Next proof step
```

**瀵?UEAgentKit 鐨勫惎鍙?*锛堢洿鎺ラ€傜敤锛夛細

1. **Fact Graph 涓嶆槸"鑱婂ぉ鍘嗗彶"涔熶笉鏄?鏈€缁堢粨璁?锛岃€屾槸鍙拷婧殑渚濊禆閾?*锛?

```text
Observation
鈫?Hypothesis
鈫?Change / Experiment
鈫?Evidence
鈫?Verified / Rejected Fact
鈫?Decision
鈫?Next Plan
```

Danus 鍦ㄦ暟瀛﹁瘉鏄庯紙姣旀父鎴忕爺鍙戞洿闇€瑕佷弗鏍兼帹鐞嗙殑棰嗗煙锛夐獙璇佷簡杩欎釜妯″紡鏈夋晥銆俇EAgentKit
宸茬粡鍦ㄥ啓 Change Set銆乀rust Verdict銆丼emantic Diff銆両mpact Analysis鈥斺€旈兘鏄?
纭畾鎬?Evidence锛?*鍙槸杩樻病缁勭粐鎴?Hypothesis 鈫?Evidence 鈫?Verdict 鐨勯摼鏉?*銆?

2. **Stateless Verifier 浼樺厛浜?LLM Judge**锛?

Danus 鐨?verifier 鏄?Lean 4 褰㈠紡鍖栬瘉鏄庢鏌ュ櫒锛岃緭鍏?proof 杈撳嚭浜屽€煎垽瀹氾紙閫氳繃/澶辫触锛夈€?
UEAgentKit 鐨勫搴斿眰鏄細

```text
C++ 鈫?UBT / compiler锛堜簩鍊硷級
Blueprint 鈫?Blueprint Compile锛堜簩鍊硷級
Asset 鈫?resident read-back + SHA-256锛堜簩鍊硷級
Gameplay 鈫?PIE + Automation锛堝ぇ閮ㄥ垎浜屽€硷紝灏戞暟闇€闃堝€硷級
Performance 鈫?benchmark delta锛堥渶闃堝€煎垽瀹氾級
```

杩欎笌褰撳墠 R3 Trust / W2 Fast Verify / W3 Strong Verify 鐨勬柟鍚戜竴鑷达紝**搴旂户缁己鍖?*锛?
鑰屼笉鏄紩鍏ュ彟涓€涓?LLM 鍋?Judge銆?

3. **闀夸换鍔′笉渚濊禆鑱婂ぉ涓婁笅鏂?*锛?

Danus 鐨?worker agents 骞惰璇佹槑鏃讹紝鍙粠 fact graph 璇诲彇鐩稿叧宸查獙璇佸紩鐞嗭紝鑰屼笉鏄
鏁翠釜瀵硅瘽鍘嗗彶銆傚搴?UEAgentKit 搴旀寜浠诲姟鍙姞杞界浉鍏筹細

```text
褰撳墠璧勪骇 / Revision
宸查獙璇?Engineering Facts锛堢敱 Trust Verdict 浜у嚭锛?
鐩稿叧 Design Decisions
Known Issues
鏈€杩?Benchmark
褰撳墠 Change Set / Active Work
涓庡綋鍓?Hypothesis 鏈変緷璧栧叧绯荤殑 Evidence
```

杩欐瘮缁х画鎵╁ぇ榛樿 Context Pack 鏇撮噸瑕併€?

**Danus 鐨勬灦鏋勫 Track M 鐨勫叿浣撴寚瀵?*锛?

- M2 涓嶅彧鏄€孡0 浜嬩欢绱㈠紩銆嶏紝杩樿鍔?`hypothesis_id` 瀛楁鍜?evidence chain 琛?
- M3 钂搁鏃朵笉鍙槸銆屾彁鍙栦簨瀹炪€嶏紝杩樿璇嗗埆鍝簺 evidence 鏀寔/鍙嶉┏鏌愪釜 hypothesis
- M5 娉ㄥ叆鏃讹紝浼樺厛娉ㄥ叆銆屼笌褰撳墠浠诲姟鐩稿叧鐨?verified facts + 瀵瑰簲 hypothesis銆嶏紝
  鑰屼笉鏄叏閮?L2 / L3

鍙傝€冩潵婧愶細
[Danus 璁烘枃](https://arxiv.org/abs/2607.06447) 路
[FrenzyMath GitHub](https://github.com/frenzymath) 路
[Rethlas (informal reasoning)](https://github.com/frenzymath/Rethlas) 路
[Archon (formal verification)](https://github.com/frenzymath/Archon)

### 2.1 TencentDB Agent Memory锛歀0鈫扡3 鍥涘眰娓愯繘閲戝瓧濉?

```text
L0 Conversation   鍘熷浜や簰 / 宸ュ叿璋冪敤鏃ュ織
L1 Atom           鍘熷瓙璁板繂锛氬崟鏉″彲澶嶇敤浜嬪疄
L2 Scenario       鍦烘櫙锛氭妸澶氭潯 Atom 鑱氬悎鎴?鏌愮被浠诲姟鎬庝箞鍋?
L3 Persona        闀挎湡鍋忓ソ / 椤圭洰椋庢牸
```

鏄犲皠鍒?UEAgentKit锛?*鍏抽敭锛氫笉鏂板缓骞宠浣撶郴锛屽鐢ㄥ凡鏈夎祫浜?*锛夛細

```text
L0  鈫? 宸叉湁 live-write-journal / receipts / checkpoints / Change Set
       锛堣繖浜涘凡缁忔槸缁撴瀯鍖栥€佸彲鎭㈠銆佸甫 Revision 鐨勯珮璐ㄩ噺鍘熷鏃ュ織锛?
L1  鈫? 宸叉湁 memory_records锛坧rojectFact / knownIssue / decisionRecord锛?
L2  鈫? 鏂板锛氫粠 Change Set + Trust Verdict 钂搁"浠诲姟閰嶆柟"锛圧ecipe锛?
L3  鈫? 鏂板锛氶」鐩骇绾﹀畾锛堝懡鍚嶈鑼冦€丳olicy 鍋忓ソ銆佸父鐘敊璇級
```

UEAgentKit 鐩告瘮閫氱敤璁板繂搴撴湁涓喅瀹氭€т紭鍔匡細**L0 灞備笉鏄嚜鐒惰瑷€瀵硅瘽锛岃€屾槸甯?SHA-256
Revision 鍜?Trust Verdict 鐨勭‘瀹氭€ц瘉鎹?*銆傝捀棣忓嚭鐨?L1/L2 澶╃敓鍙獙璇併€佸彲鑷姩 stale锛?
涓嶉渶瑕?LLM 鐚滄祴浜嬪疄鏄惁鎴愮珛銆?

### 2.2 閲囩撼锛氭湰鍦?FTS5 + 鍚戦噺娣峰悎鍙洖锛岄浂澶栭儴 API

鍙傝€冨疄鐜扮敤 FTS5 + 鏈湴宓屽叆 + BM25/鍚戦噺 RRF 铻嶅悎锛屼笉渚濊禆澶栭儴 API銆俇EAgentKit 宸叉湁 FTS5锛?
鍙渶琛ュ悜閲忓眰銆?*蹇呴』淇濇寔 `dependencies = []` 鐨勯浂渚濊禆搴曠嚎**锛氬悜閲忚兘鍔涘仛鎴?
`optional-dependencies`锛岀己澶辨椂鑷姩闄嶇骇涓虹函 FTS5锛屼换浣曢棬绂佷笉寰楀洜姝ゅけ璐ャ€?

### 2.2.1 銆愭渶楂樹紭鍏堢害鏉熴€戜笉寰楀鍔犱换鍔¤捣姝㈠紑閿€

鐢ㄦ埛宸叉槑纭弽棣堣繃寰€鍚岀被璁板繂搴撶殑瀹為檯闂锛?

> 姣忔浠诲姟寮€濮嬪拰缁撴潫 AI 閮戒細鑺卞緢闀挎椂闂存潵澶勭悊锛屽鑷存晥鐜囦綆涓嬨€乀oken 寮€閿€涔熷ぇ銆?

杩欐槸鏈?Track 鐨?*鍚﹀喅鎬х害鏉?*锛氫换浣曡浠诲姟寮€濮嬪彉鎱€佺粨鏉熷彉鎱㈢殑璁捐涓€寰嬩笉閲囩撼锛?
鍗充娇浼氱壓鐗茶蹇嗗畬鏁村害銆傚弬鑰冨疄鐜伴噷鎭板ソ鏈夊洓涓拡瀵规€ф満鍒讹紝鍏ㄩ儴閲囩撼锛?

**(1) 鍒嗗眰娉ㄥ叆 vs 宸ュ叿鍖?鈥斺€?鍙敞鍏?L2/L3锛孡0/L1 涓€寰嬪伐鍏峰寲**

```text
L3 椤圭洰绾﹀畾      娉ㄥ叆锛堟瀬灏忥紝绋冲畾锛屽嚑涔庝笉鍙橈級
L2 浠诲姟閰嶆柟      娉ㄥ叆锛堜粎褰撳墠浠诲姟鍩熷懡涓殑 1-3 鏉℃憳瑕侊級
L1 鍘熷瓙浜嬪疄      涓嶆敞鍏ワ紝浣滀负 Tool 渚涙ā鍨嬫寜闇€鏌?
L0 鍘熷璇佹嵁      涓嶆敞鍏ワ紝浣滀负 Tool 渚涙ā鍨嬫寜闇€鏌?
```

鍙傝€冨疄鐜版槑纭啓鍑鸿繖鏍峰仛鐨勭悊鐢憋細閬垮厤涓婃父 KV-cache 澶辨晥銆傛敞鍏ュ唴瀹瑰繀椤荤ǔ瀹氫笖鏋佸皬锛?
鍚﹀垯姣忚疆閮藉湪鐮村潖 prompt 缂撳瓨鈥斺€旇繖姝ｆ槸"姣忔閮藉緢鎱?鐨勬牴鍥犱箣涓€銆?

**(2) 鍐欏洖蹇呴』寮傛锛岀粷涓嶉樆濉炰换鍔＄粨鏉?*

鍙傝€冨疄鐜板湪浜虹被鍥炲悎缁撴潫鍚?*寮傛**鍐欏洖 L0锛岃捀棣忓湪鍚庡彴杩涜銆俇EAgentKit 瀵瑰簲鍋氭硶锛?

```text
浠诲姟缁撴潫鏃?         鍙仛涓€娆¤拷鍔犲啓锛坅ppend-only锛夛紝O(1)锛屼笉鍋氫换浣曟娊鍙?
L1 钂搁             鍚庡彴 / 涓嬫绌洪棽 / 鏄惧紡鍛戒护瑙﹀彂锛屼笉鍦ㄤ换鍔￠摼璺笂
L2/L3 钂搁          鏄惧紡鍛戒护鎴栧畾鏈熻Е鍙戯紝缁濅笉闅愬紡鍙戠敓
```

**(3) 纭€ч绠楋細鏉℃暟 + 瀛楃 + 瓒呮椂涓夐噸涓婇檺**

鍙傝€冨疄鐜板鍙洖缁撴灉鍚屾椂鏂藉姞 item count銆乧haracter budget銆乼imeout 涓夐噸闄愬埗銆?
UEAgentKit 蹇呴』鍦?Server 渚у己鍒讹紙娌跨敤鐜版湁 Token Budget 鏈哄埗锛夛細

```text
鍚姩娉ㄥ叆        鈮?800 Token 纭笂闄愶紝瓒呭嚭鍗虫埅鏂紝涓嶅緱鍗忓晢
鍗曟鍙洖        鈮?5 鏉?/ 鈮?2000 瀛楃 / 鈮?300 ms 瓒呮椂
瓒呮椂琛屼负        杩斿洖宸插緱缁撴灉 + 鏄惧紡 truncated 鏍囪锛岀粷涓嶇瓑寰?
```

**(4) 鍐峰惎鍔ㄩ浂鎴愭湰锛氭棤鍛戒腑鏃朵笉鍋氫换浣曚簨**

鏂伴」鐩€佹棤璁板繂銆佹垨褰撳墠浠诲姟鍩熸棤鍛戒腑鏃讹紝娉ㄥ叆鍐呭蹇呴』涓虹┖瀛楃涓诧紝涓嶅緱杈撳嚭
"鏆傛棤璁板繂"涔嬬被鍗犱綅鏂囨湰锛屼篃涓嶅緱瑙﹀彂浠讳綍妫€绱㈡垨寤哄簱鍔ㄤ綔銆?

**楠屾敹闂ㄧ锛圵4 涔嬪悗銆丮emory 瀹炵幇鏈熼棿鎸佺画娴嬮噺锛?*锛?

```text
[ ] Memory 鍏抽棴 vs 寮€鍚紝浠诲姟棣栦釜 Tool 璋冪敤鐨勯澶栧欢杩?< 200 ms
[ ] Memory 鍏抽棴 vs 寮€鍚紝鍚姩娉ㄥ叆鐨勯澶?Token < 800
[ ] 浠诲姟缁撴潫鐨勯澶栬€楁椂 < 100 ms锛堜粎 append 鍐欙級
[ ] 浠讳竴鎸囨爣瓒呮爣 鈫?璇ラ樁娈靛垽瀹?blocked锛屼笉寰楄繘鍏ヤ笅涓€闃舵
```

杩欏闂ㄧ蹇呴』鍦?M1 闃舵灏卞缓绔嬫祴閲忚剼鏈紝鑰屼笉鏄瓑瀹炵幇瀹屽啀琛ャ€?

### 2.3 閲囩撼锛氱鍙峰寲涓婁笅鏂囧帇缂?

鍙傝€冨疄鐜版妸鍐楅暱宸ュ叿鏃ュ織鍘嬫垚 Mermaid 绗﹀彿浠ョ渷 Token銆俇EAgentKit 鐨勫搴斿満鏅槸
Change Set / Impact Analysis / Semantic Diff 鐨勫ぇ JSON鈥斺€旇繖浜涙鏄綋鍓嶆渶鍗犱笂涓嬫枃鐨勯儴鍒嗐€?

### 2.4 閲囩撼锛歋cope 鍒嗗眰锛坱eam / user / agent / visibility锛?

瀵瑰簲 0.9 宸茶鍒掔殑 `/project` `/team` `/user` `/session` 鍒嗗眰锛屽彲鐩存帴娌跨敤鍏舵潈闄愭ā鍨嬨€?

### 2.5 鎷掔粷锛歀LM 鑷敱鎶藉彇鍐欏叆鐭ヨ瘑搴?

鍙傝€冨疄鐜颁緷璧?LLM 浠庡璇濋噷鎶藉彇璁板繂銆俇EAgentKit 鐨?`user-confirmed / tool-observed /
model-inferred` 鏉ユ簮鍒嗙骇鍜?Revision stale 鏈哄埗鏄棦鏈夎祫浜э紝**涓嶈兘涓轰簡鑷姩绉疮鑰屾斁寮?*銆?
鏈鍒掔殑鑷姩鎹曡幏鍏ㄩ儴璧?`tool-observed`锛堢‘瀹氭€ф潵婧愶級锛宍model-inferred` 浠嶉渶鏄惧紡鏍囪
涓斾笉鍙備笌榛樿鍙洖銆?

杩欐潯鍚屾椂涔熸槸鏁堢巼绾︽潫锛?*UEAgentKit 鐨?L0鈫扡1 钂搁涓嶉渶瑕佽皟鐢?LLM**銆侰hange Set銆?
Trust Verdict銆丼emantic Diff 宸茬粡鏄粨鏋勫寲纭畾鎬ф暟鎹紝鐢ㄨ鍒欏嵆鍙彁鍙栦簨瀹炪€?
涓嶈皟 LLM 鎰忓懗鐫€钂搁鎴愭湰鎺ヨ繎闆讹紝杩欐槸 UEAgentKit 鐩稿閫氱敤璁板繂搴撶殑缁撴瀯鎬т紭鍔裤€?

### 2.6 鎷掔粷锛氫簯绔?/ 澶栭儴鏁版嵁搴撲緷璧栦笌 Proxy 鏋舵瀯

鍙傝€冨疄鐜扮敤 MemoryProxy 鎷︽埅 LLM 璇锋眰鍋氶€忔槑娉ㄥ叆锛岄厤濂?MemoryCore/Hub/Panel 鍥涗釜鏈嶅姟銆?
Redis/COS 瀛樺偍涓庡鑺傜偣閮ㄧ讲銆俇EAgentKit **涓嶉噰绾宠繖濂楁灦鏋?*锛?

```text
涓嶅紩鍏?LLM 璇锋眰浠ｇ悊     鐜版湁闆嗘垚鐐规槸 MCP Server锛屾敞鍏ラ€氳繃 Tool 杩斿洖鍊煎畬鎴?
涓嶅紩鍏?Redis / COS      鍗曚汉鏈湴鍦烘櫙 SQLite 瓒冲
涓嶅紩鍏ョ嫭绔嬫湇鍔￠泦缇?     MCP Server 杩涚▼鍐呭畬鎴愶紝閬垮厤杩愮淮璐熸媴
涓嶅紩鍏ヤ簯绔瓨鍌?         鍥哄畾椤圭洰 + 鏈湴 stdio + 鏃犲嚭绔欐槸鐜版湁瀹夊叏妯″瀷鍩虹
```

### 2.7 閲囩撼锛歐iki / CodeGraph 鐨?宸ュ叿鍖栬€岄潪娉ㄥ叆"鎬濊矾

鍙傝€冨疄鐜版妸鏂囨。缁勭粐鎴愬彲妫€绱?Wiki锛屾妸浠ｇ爜搴撶储寮曟垚鍚枃浠?绗﹀彿/璋冪敤鍏崇郴鐨?CodeGraph锛?
涓よ€呴兘鍙綔涓?Tool 鎸夐渶璇诲彇锛屼笉鏁翠綋娉ㄥ叆銆?

UEAgentKit 宸茬粡鏈夌瓑浠风墿涓旀洿寮猴細SQLite 绱㈠紩閲岀殑 Asset / Symbol / Reference 灏辨槸
鐜版垚鐨?CodeGraph锛宍ue_search_*` / `ue_get_references` 灏辨槸鐜版垚鐨勬寜闇€宸ュ叿銆?
**杩欓儴鍒嗘棤闇€鏂板缓锛屽彧闇€鍦?Track V 閲岀粰瀹冧竴涓彲瑙嗗寲鍏ュ彛銆?*

### 2.8 鍙傝€冭竟鐣屽０鏄?

鏈湴鍙傝€冨壇鏈綅浜?`E:\WorkSpace\TencentDB-Agent-Memory`锛?*浠呯敤浜庣爺绌舵灦鏋勪笌璁捐鎬濊矾**锛?
閫傜敤 [`docs/REFERENCE_POLICY.md`](../REFERENCE_POLICY.md) 鐨勬棦鏈夎鍒欙細涓嶅鍒朵唬鐮併€?
涓嶇Щ妞嶅疄鐜般€佷笉寮曞叆鍏朵緷璧栥€傛湰璁″垝閲囩撼鐨勬槸鍒嗗眰绛栫暐銆佹敞鍏?宸ュ叿鍖栬竟鐣屻€佸紓姝ュ啓鍥炰笌
棰勭畻绾︽潫杩欎簺**璁捐鍐崇瓥**锛屽叿浣撳疄鐜板叏閮ㄧ嫭绔嬬紪鍐欍€?

鍙傝€冩潵婧愶細
[TencentDB-Agent-Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) 路
[MemoryCore README](https://github.com/TencentCloud/TencentDB-Agent-Memory/blob/feat/server_team/MemoryCore/README.md) 路
[鍥涘眰绠＄嚎璇存槑](https://www.marktechpost.com/2026/05/23/tencent-open-sources-tencentdb-agent-memory-a-4-tier-local-memory-pipeline-for-ai-agents/) 路
[Mermaid 涓婁笅鏂囧嵏杞絔(https://www.tencentcloud.com/techpedia/144098) 路
[鑵捐浜戣祫浜х鐞嗗洓绫昏祫浜(https://cloud.tencent.com/document/product/1813/134591) 路
[FTS5+鍚戦噺娣峰悎鍙洖瀹炵幇鍙傝€僝(https://github.com/baodq97/tencentdb-agent-memory)

## 3. Track 鍒掑垎涓庡垎鏀瓥鐣?

鍥涙潯 Track锛屼笁涓苟琛屽垎鏀€傚垎鏀殧绂诲師鍒欐部鐢?Post-0.8 璁″垝绗?2 鑺傦細涓?Rebase 宸插叡浜?
鍒嗘敮锛屽悓姝ュ彧鐢ㄦ槑纭?checkpoint merge銆?

```text
Track W  Writer 鑳藉姏          feature/live-writer-expansion   鈫?褰撳墠娲昏穬锛屾渶楂樹紭鍏?
Track M  Memory 鑷姩绉疮      feature/memory-context          鈫?宸插瓨鍦ㄨ繙绋嬪垎鏀紝澶嶇敤
Track C  P4 鍗忎綔鎰熺煡          feature/source-control-p4       鈫?鏂板缓
Track V  鐭ヨ瘑搴?Web 鍙娴忚   feature/knowledge-web-view      鈫?鏂板缓
```

### 3.1 骞惰瀹夊叏鎬у垎鏋?

| Track | 瑙︾ C++ Plugin | 瑙︾ agent_workflow.py | 瑙︾ memory_* | 鍐茬獊椋庨櫓 |
|---|---|---|---|---|
| W | 鏄紙EditorBridge 鍐欒矾寰勶級 | 鏄紙閲嶅害锛?| 鍙璋冪敤 | 鈥?|
| M | 鍚?| 浠呮柊澧?hook 璋冪敤鐐?| 鏄紙閲嶅害锛?| 涓?W 浣?|
| C | 鏄紙鏂?SourceControl handler锛?| 鍚?| 鍚?| 涓?W 涓?|
| V | 鍚?| 鍚?| 鍙 | 鏋佷綆 |

**鎺掓湡绾︽潫**锛?
- Track V 鍙珛鍗充笌 W 骞惰锛屾棤鍐茬獊銆?
- Track M 闇€鍦?W4 鐨?Change Set 缁撴瀯鍐荤粨鍚庡啀杩涘叆瀹炵幇锛堝惁鍒?L0 钂搁婧愪細鍙橈級锛?
  浣?*璁捐涓?Schema 鍙彁鍓嶈繘琛?*銆?
- Track C 瑙︾ C++锛屽缓璁瓑 W4 鐨?C++ 鏀瑰姩钀藉湴鍚庡惎鍔紝閬垮厤 EditorBridge 鍙岀嚎鍐茬獊銆?

鎺ㄨ崘鍚姩椤哄簭锛?

```text
T0 W3 鏀跺彛 checkpoint     鈫?complete (`45e6ea2`)
W4-0 鈥?W4-7              鈫?绔嬪嵆锛屼富绾?
Track V                  鈫?涓?W4 骞惰锛堥浂鍐茬獊锛?
Track M 璁捐闃舵         鈫?涓?W4 骞惰锛堢函鏂囨。 + Schema 璁捐锛?
Track M 瀹炵幇闃舵         鈫?W4 Change Set 鍐荤粨鍚?
Track C                  鈫?W4 C++ 钀藉湴鍚?
```

## 4. Track W 鈥?Writer 鑳藉姏锛堟渶楂樹紭鍏堬級

### T0锛歐3 鏀跺彛 checkpoint锛坈omplete锛?

T0 宸蹭簬 2026-08-27 瀹屾垚銆傚疄闄呮彁浜よ竟鐣屼互纾佺洏 Git 鍘嗗彶涓哄噯锛?

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

鏀跺彛鏃?712/712 Python suite銆乣ValidateRelease.py` 0.7.0銆乁E5.6 Direct Build 涓?`git diff --check` 鍏ㄩ儴閫氳繃銆傛病鏈?Push銆丷ebase銆乀ag 鎴?Release銆俉4 鐨勫疄鐜板熀绾垮浐瀹氫负 `45e6ea2`锛涘悗缁鍒掓枃妗ｄ笉灞炰簬 W3 checkpoint銆?

### W4锛氬鎿嶄綔 / 鏈夌晫鎵归噺锛堜富绾匡紝璇︾粏璁″垝宸插喕缁擄級

W4 璇︾粏璁″垝瑙?
[`UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`](Archive/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md)锛?
鏈富璁″垝涓嶉噸澶嶅叾鍐呭锛屽彧鍥哄畾瀹冨湪椤圭洰灞傜殑浣嶇疆涓庢帓鏈熴€?

闃舵瀹氫箟浠?W4 璇︾粏璁″垝绗?10銆?5 鑺備负鍞竴鏉冨▉鏉ユ簮锛涙湰涓昏鍒掍笉缁存姢绗簩濂楅樁娈电紪鍙枫€傚綋鍓嶆潈濞侀樁娈典负锛?

```text
W4-0  Contract Freeze and Baseline
W4-1  Bounded Batch Plan
W4-2  Single-Asset Multi-operation Apply
W4-3  Multi-Asset Resident Apply
W4-4  Multi-Asset Checkpoint Save
W4-5  Aggregate Strong Verify / Semantic Diff / Trust
W4-6  Recovery and Restart Hardening
W4-7  Full Acceptance / Documentation
```

**椤圭洰灞傝ˉ鍏呯害鏉?*锛圵4 璁″垝鏈兜鐩栵紝浣嗗鍚庣画 Track 鑷冲叧閲嶈锛夛細

W4 缁撴潫鏃跺繀椤诲喕缁撳苟鏂囨。鍖?Change Set 鐨勬渶缁堢粨鏋勶紝鍥犱负 Track M 鐨?L0 钂搁鐩存帴璇诲彇瀹冿細

```text
[ ] Change Set schema 鐗堟湰鍙锋樉寮忛€掑骞跺啓鍏?CHANGELOG
[ ] batch receipt 鐨勫瓧娈甸泦鍐荤粨锛屾爣娉ㄥ摢浜涘瓧娈垫槸 Memory 钂搁濂戠害
[ ] partial-applied / partial-saved 鐨勬寔涔呭寲鏍煎紡鍐荤粨
```

缂鸿繖涓€姝ワ紝Track M 浼氬湪 W5 鏈熼棿琚弽澶嶇牬鍧忋€?

### W5锛氱湡瀹為」鐩獙鏀?+ 瑙勬ā鍩哄噯锛圵4 鍚庯紝鈮?0 澶╋級

鍦?Reforge 鐪熷疄宸ョ▼涓婅窇 W4 鎵归噺鍐欏叆锛岄噰闆嗭細

```text
鍗曟搷浣?/ 5 鎿嶄綔 / 20 鎿嶄綔 鐨?绔埌绔欢杩熷垎瑙?
甯搁┗ Apply vs Cold Commandlet 鐨勫疄娴嬪€嶇巼
160-180 GB 宸ョ▼涓嬬殑 checkpoint save + strong verify 鑰楁椂
50 MB/s HDD 妗ｄ綅鐨勯€€鍖栨洸绾?
```

W5 鐨勮緭鍑烘槸 Memory Track 鐨?L2 钂搁绱犳潗鏉ユ簮涔嬩竴锛堢湡瀹炲け璐ユ渚嬶級銆?

## 5. Track M 鈥?Memory 鑷姩绉疮锛圵4 涔嬪悗锛?

鍒嗘敮锛歚feature/memory-context`锛堣繙绋嬪凡瀛樺湪锛屽鐢級

### 5.0 璁捐绔嬪満

褰撳墠璁板繂绯荤粺鏄?Agent 鑷鍐欍€丗TS5 鍏抽敭璇嶆煡"鐨勮鍔ㄥ簱銆俆rack M 瑕佹妸瀹冨彉鎴?
"鐢ㄥ氨浼氱Н绱€佹煡寰楀噯銆佷笖涓嶆嫋鎱换鍔?鐨勪富鍔ㄥ簱銆?

涓変釜涓嶅彲濡ュ崗鐨勮竟鐣岋細

```text
1. 鏁堢巼浼樺厛浜庡畬鏁村害   浠讳綍鎷栨參浠诲姟璧锋鐨勬満鍒朵竴寰嬬爫鎺夛紙瑙?2.2.1锛?
2. 纭畾鎬т紭鍏堜簬瑕嗙洊搴? 鑷姩鍐欏叆鍙敤 tool-observed锛屼笉鐢?LLM 鎶藉彇
3. 鍏煎浼樺厛浜庨噸鏋?    Schema v3 鐨?12 寮犺〃涓嶅姩锛屽彧鍋氬姞娉?
```

### M1锛氭晥鐜囧熀绾夸笌棰勭畻闂ㄧ锛? 澶╋紝蹇呴』鏈€鍏堝仛锛?

**鍏堝缓娴嬮噺锛屽啀璋堝姛鑳姐€?* 杩欎竴闃舵涓嶅啓浠讳綍璁板繂鍔熻兘锛屽彧寤虹珛鑳借瘉鏄?娌℃湁鍙樻參"鐨勬爣灏恒€?

浜や粯锛?

```text
scripts/MeasureMemoryOverhead.py
  鈫?瀵规瘮 Memory 鍏抽棴 / 寮€鍚紙褰撳墠 v3 瀹炵幇锛変袱绉嶆ā寮?
  鈫?娴嬮噺锛氬惎鍔ㄦ敞鍏?Token 鏁般€侀涓?Tool 璋冪敤寤惰繜銆佷换鍔＄粨鏉熻€楁椂
  鈫?杈撳嚭纭畾鎬?JSON 鎶ュ憡锛岀撼鍏ュ父瑙勯棬绂?
```

鍚屾椂淇鐜扮姸闂锛氬綋鍓?`ue_memory_get_context` 娌℃湁寮哄埗涓婇檺锛孉gent 鍙兘涓€娆℃媺鍥?
杩囧鍐呭銆侻1 瑕佺粰瀹冭ˉ涓?2.2.1 鐨勪笁閲嶉绠椼€?

楠屾敹锛?

```text
[ ] 鍩虹嚎鎶ュ憡浜у嚭锛岃褰曞綋鍓?v3 鐨勫疄闄呭紑閿€
[ ] ue_memory_get_context 寮哄埗 鈮?800 Token / 鈮?5 鏉?/ 鈮?300 ms
[ ] 瓒呴绠楄繑鍥?truncated 鏍囪鑰岄潪鎶ラ敊
[ ] 鏃犺蹇嗘椂杩斿洖绌猴紝涓嶄骇鐢熷崰浣嶆枃鏈?
```

### M2锛歀0 鑷姩鎹曡幏 + Evidence Chain锛? 澶╋級

**鏍稿績鎬濊矾锛氫笉鏂板缓 L0 瀛樺偍锛屾妸宸叉湁鐨勭‘瀹氭€ф棩蹇楄瀹氫负 L0锛屽苟缁勭粐鎴愬彲杩芥函鐨?
Evidence Chain銆?*

UEAgentKit 宸茬粡鍦ㄥ啓杩欎簺涓滆タ锛屽畠浠瘮瀵硅瘽鏃ュ織璐ㄩ噺楂樺緱澶氾細

```text
live-write-journal/<receipt>.json     姣忔瀹為檯淇敼
checkpoints/<checkpointId>.json       淇濆瓨 + 寮洪獙璇佺粨鏋?
Change Set                            浠诲姟绾т慨鏀规壒娆?
Trust Verdict                         楠岃瘉缁撹
Semantic Diff                         璇箟鍙樻洿
Impact Analysis                       褰卞搷鑼冨洿
```

M2 鍋氫袱浠朵簨锛?

1. 鍦ㄨ繖浜涗骇鐗╄惤鐩樻椂锛?*杩藉姞涓€鏉℃瀬灏忕殑绱㈠紩璁板綍**锛坅ppend-only锛孫(1)锛夛紝
   鎸囧悜宸叉湁鏂囦欢锛屼笉澶嶅埗鍐呭
2. **鏄惧紡璁板綍 Hypothesis 鈫?Evidence 鈫?Verdict 鐨勯摼鏉?*锛堝€熼壌 Danus Fact-Graph锛?

鏂板琛紙Schema 杩佺Щ v3 鈫?v4锛岀函鍔犳硶銆侻4 鐨勫悜閲忚〃鍙﹀崰 v5锛屼笉鍏辩敤鐗堟湰鍙凤級锛?

```sql
CREATE TABLE memory_l0_events (
    event_id        TEXT PRIMARY KEY,
    project_key     TEXT NOT NULL,
    event_kind      TEXT NOT NULL,     -- live_write | checkpoint | change_set
                                       -- | trust | semantic_diff | impact
    occurred_at_utc TEXT NOT NULL,
    artifact_path   TEXT NOT NULL,     -- 鎸囧悜宸叉湁 JSON锛屼笉澶嶅埗鍐呭
    asset_paths     TEXT NOT NULL,     -- JSON 鏁扮粍
    change_set_id   TEXT,
    hypothesis_id   TEXT,              -- 鍏宠仈鐨勫亣璁撅紙鍙┖锛涙柊澧炲瓧娈碉級
    outcome         TEXT NOT NULL,     -- success | failed | rejected | superseded
    distilled       INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_key, event_kind, artifact_path)
);
CREATE INDEX memory_l0_pending_idx
    ON memory_l0_events(project_key, distilled, occurred_at_utc);
CREATE INDEX memory_l0_hypothesis_idx
    ON memory_l0_events(hypothesis_id) WHERE hypothesis_id IS NOT NULL;

-- 鏂板锛欵ngineering Evidence Chain锛堝€熼壌 Danus Fact-Graph锛?
CREATE TABLE memory_evidence_chains (
    chain_id        TEXT PRIMARY KEY,
    project_key     TEXT NOT NULL,
    hypothesis      TEXT NOT NULL,     -- 鍋囪锛?GameThread spike 鍙兘鐢辨煇绫?Actor Tick 瀵艰嚧"
    context         TEXT NOT NULL,     -- JSON锛氱浉鍏宠祫浜с€佹寚鏍囥€佸凡鐭ョ害鏉?
    verdict         TEXT NOT NULL,     -- supported | rejected | inconclusive
    confidence      TEXT NOT NULL,     -- high | medium | low锛堝熀浜?evidence 鏁伴噺涓庣被鍨嬶級
    created_at_utc  TEXT NOT NULL,
    verified_at_utc TEXT,              -- 鍒ゅ畾鏃堕棿锛坴erdict != inconclusive 鍚庡～鍏咃級
    superseded_by   TEXT,              -- 琚悗缁疄楠屾帹缈绘椂鎸囧悜鏂?chain_id
    FOREIGN KEY(superseded_by) REFERENCES memory_evidence_chains(chain_id)
);
CREATE INDEX memory_evidence_verdict_idx
    ON memory_evidence_chains(project_key, verdict, verified_at_utc);
```

鍐欏叆鐐癸紙鍏ㄩ儴鏄凡鏈変唬鐮佽矾寰勪笂鍔犱竴琛岋級锛?

```text
agent_workflow.py  鍐?live-write journal 鍚?
                   鍐?checkpoint record 鍚?
                   Change Set 鐘舵€佸彉鏇村悗
                   Trust Verdict 浜у嚭鍚?

鏂板 hypothesis 鍐欏叆鐐癸紙鍙€夛紝涓嶉樆濉?M2 浜や粯锛夛細
  Agent 鍦?Plan 闃舵鏄庣‘璇?鎴戠寽娴?X 瀵艰嚧 Y"鏃讹紝鍒涘缓 chain
  鍚﹀垯 L0 鐨?hypothesis_id 瀛楁鐣欑┖锛孧3 钂搁鏃跺敖鍔涙帹鏂?
```

**鏁堢巼淇濊瘉**锛氬崟鏉?INSERT锛屾棤绱㈠紩閲嶅缓锛屾棤 LLM锛屾棤缃戠粶銆傚疄娴嬪簲 < 5 ms銆?

楠屾敹锛?

```text
[ ] 涓€娆?W4 鎵归噺鍐欏叆鍚庯紝L0 浜嬩欢瀹屾暣璁板綍涓?outcome 姝ｇ‘
[ ] 澶辫触 / 鎷掔粷 / superseded 璺緞鍚屾牱琚褰曪紙澶辫触妗堜緥鏄渶鏈変环鍊肩殑璁板繂锛?
[ ] hypothesis_id 瀛楁瀛樺湪涓斿彲鍏宠仈鍒?memory_evidence_chains
[ ] 浠诲姟缁撴潫棰濆鑰楁椂 < 100 ms
[ ] Memory 鍏抽棴鏃堕浂鍐欏叆銆侀浂寮€閿€
[ ] 鍏抽棴 Memory 鍚庡啀寮€鍚紝鍘嗗彶浜嬩欢涓嶄涪
[ ] Schema v3 鏁版嵁搴撳彲鍘熷湴鍗囩骇鍒?v4
```

### M3锛歀0鈫扡1 瑙勫垯钂搁 + Evidence 鍏宠仈锛? 澶╋級

**绂荤嚎鎵ц锛屼笉鍦ㄤ换鍔￠摼璺笂銆?* 瑙﹀彂鏂瑰紡涓夌锛岄兘涓嶆槸闅愬紡鐨勶細

```text
鏄惧紡鍛戒护      ue-agent memory distill
绌洪棽瑙﹀彂      MCP Server 绌洪棽 > 30 s 涓旀湁 pending L0 鏃讹紝鍚庡彴鍗曟壒澶勭悊
涓嬫鍚姩      鍚姩鏃惰嫢 pending > 闃堝€硷紝鍚庡彴寮傛澶勭悊锛堜笉闃诲棣栦釜璇锋眰锛?
```

瑙勫垯钂搁锛?*闆?LLM**锛屽叏閮ㄤ粠缁撴瀯鍖栧瓧娈垫帹瀵硷級锛?

| L0 鏉ユ簮 | 鎻愬彇瑙勫垯 | 浜у嚭 L1 璁板綍 | Evidence 璇箟 |
|---|---|---|---|
| 鎴愬姛 live_write + Trust verified | 璧勪骇绫?+ 鎿嶄綔 + 鐩爣 + 鐢熸晥鍊?| `projectFact`锛氳璧勪骇姝ゅ睘鎬у綋鍓嶅€间笌鏉ユ簮 | 濡傚叧鑱?hypothesis锛屾爣璁颁负 supporting evidence |
| 澶辫触 / 鎷掔粷 | 鎷掔粷鍘熷洜鐮?+ 涓婁笅鏂?| `knownIssue`锛氫粈涔堟潯浠朵笅浼氳鎷?| 濡傚叧鑱?hypothesis锛屾爣璁颁负 contradicting evidence |
| Policy 鎷掔粷 | Policy 瑙勫垯 + 瑙﹀彂鏉′欢 | `projectRule`锛氶」鐩疄闄呯敓鏁堢殑绾︽潫 | 鈥?|
| Semantic Diff | 鍙樻洿鍓嶅悗璇箟宸紓 | `projectFact`锛氬彉鏇村巻鍙?| 濡?delta 鏄捐憲锛屽彲浣滀负 supporting evidence |
| Impact Analysis | 娑堣垂鑰呴泦鍚?| `projectFact`锛氳璧勪骇鐨勫疄闄呭奖鍝嶉潰 | 鏍囪涓?constraint锛堢害鏉熷亣璁剧殑閫傜敤鑼冨洿锛?|
| supersession | 琚鐩栫殑鏃у€奸摼 | `decisionRecord`锛氫负浣曟敼鎴愮幇鍊?| 鏃?hypothesis 鏍?superseded |

**Evidence Chain 鐨勫垽瀹氳鍒?*锛堟柊澧烇紝鍊熼壌 Danus stateless verifier锛夛細

```text
verdict = supported
  鏉′欢锛氣墺2 鏉?supporting evidence + 0 鏉?contradicting + Trust = verified

verdict = rejected
  鏉′欢锛氣墺1 鏉?contradicting evidence锛坈ompiler error / benchmark 鍙樺樊锛?

verdict = inconclusive
  鏉′欢锛歟vidence 涓嶈冻 2 鏉★紝鎴?supporting 涓?contradicting 鍏卞瓨浣嗘棤鍐冲畾鎬ц瘉鎹?

confidence = high
  鏉′欢锛歷erdict = supported 涓旀墍鏈?evidence 鏉ヨ嚜 deterministic verifier
        锛坈ompiler / benchmark / SHA-256锛?

confidence = medium
  鏉′欢锛歷erdict = supported 浣嗛儴鍒?evidence 闇€闃堝€煎垽瀹氾紙濡?5% 鎬ц兘鏀瑰杽锛?

confidence = low
  鏉′欢锛歷erdict = supported 浣?evidence 浠?1 鏉★紝鎴栧瓨鍦ㄦ湭楠岃瘉鐨勫亣璁句緷璧?
```

鎵€鏈変骇鍑鸿褰曪細

```text
source            = tool-observed锛堢‘瀹氭€э紝闈炴帹娴嬶級
revision_set      = 鎸夎瘉鎹被鍨嬬粦瀹氬叾鐪熷疄鏉ユ簮鐨勭増鏈爣璇嗭紙瑙佷笅锛?
node_id           = 鎸夎祫浜ц矾寰勮嚜鍔ㄦ寕鍒扮煡璇嗘爲瀵瑰簲鑺傜偣
evidence_for      = chain_id锛堟柊澧烇紱鎸囧悜璇ヨ褰曟敮鎸?鍙嶉┏鐨?hypothesis锛?
```

**revision 缁戝畾蹇呴』鎸夎瘉鎹被鍨嬪尯鍒?*锛屼笉寰椾竴寰嬬粦 Asset SHA-256锛?

```text
live_write / Semantic Diff   鐩爣璧勪骇 Revision
Policy 鎷掔粷                  Policy digest
Impact Analysis              index generation + 鐩稿叧璧勪骇 Revision 闆嗗悎
Change Set / checkpoint      璇?checkpoint / Change Set 鐨?revision 闆嗗悎
P4 瑙傛祴                      provider 鐨?observation / head revision锛堝彲寰楁椂锛?
supersession                 琚鐩栧€奸摼涓ょ鐨?revision
```

鐞嗙敱锛氱粺涓€缁戣祫浜у搱甯屼細璁╂潵婧愬凡鍙樼殑璁板綍浠嶈鍒?`valid`銆備緥濡傛敼浜?
`write-policy.json`锛屼粠 Policy 鎷掔粷钂搁鍑虹殑 `projectRule` 涓嶄細杞?stale锛?
璁板繂搴撲細缁х画缁欏嚭杩囨湡瑙勫垯鈥斺€旇繖姝ｆ槸涓夋簮鏂伴矞搴︽満鍒惰闃茬殑澶辨晥妯″紡銆?
`revision_set` 鍥犳鏄鍏冪粍闆嗗悎锛岄泦鍚堝唴浠讳竴鍏冪礌澶遍厤鍗宠浆 stale銆?

鑷姩鎸傛爲瑙勫垯锛堥伩鍏嶄汉宸ョ淮鎶?Path锛夛細

```text
/Game/Characters/Hero/DA_HeroStats
  鈫?/project/content/characters/hero
璧勪骇鐩綍灞傜骇鐩存帴鏄犲皠鐭ヨ瘑鏍?Path锛岃妭鐐逛笉瀛樺湪鍒欒嚜鍔ㄥ垱寤?
```

楠屾敹锛?

```text
[ ] 钂搁瀹屽叏涓嶈皟鐢?LLM
[ ] 100 鏉?L0 钂搁鑰楁椂 < 5 s
[ ] 钂搁杩囩▼鍙腑鏂€佸彲缁窇锛坉istilled 鏍囪骞傜瓑锛?
[ ] 璧勪骇 Revision 鍙樺寲鍚庯紝鐩稿叧 L1 鑷姩杞?stale
[ ] Policy 鏂囦欢鍙樻洿鍚庯紝鐢?Policy 鎷掔粷钂搁鐨?projectRule 鑷姩杞?stale
[ ] 绱㈠紩鎹唬鍚庯紝Impact Analysis 娲剧敓璁板綍鑷姩杞?stale
[ ] 鍏被缁戝畾鍚勬湁涓€涓€屾潵婧愬彉鏇?鈫?杞?stale銆嶆祴璇曠敤渚?
[ ] 钂搁涓嶅湪浠讳綍浠诲姟鐨勫悓姝ラ摼璺笂锛堢敤娴嬮噺鑴氭湰璇佹槑锛?
[ ] 閲嶅钂搁鍚屼竴 L0 涓嶄骇鐢熼噸澶?L1
[ ] 鍏宠仈 hypothesis 鐨?L0 浜嬩欢锛屼骇鍑虹殑 L1 璁板綍甯︽纭殑 evidence_for
[ ] Evidence chain 鐨?verdict 鍒ゅ畾涓?6 绫绘彁鍙栬鍒欏悇鏈夋祴璇曠敤渚?
[ ] 浠ｇ爜灞備笉瀛樺湪鐢辩鍑?/ 閿佸畾鍘嗗彶鎺ㄥ owner / maintainer 鐨勮矾寰?
```

**绂佹椤?*锛?

```text
涓嶅緱鎶婁换涓€璇佹嵁绫诲瀷缁熶竴缁戝埌 Asset SHA-256
涓嶅緱鎶婅娴嬪埌鐨勪汉鍛樼鍑?/ 閿佸畾鍘嗗彶钂搁涓恒€岃礋璐ｄ汉 / 缁存姢鑰呫€嶆寔涔呮柇瑷€
涓嶅緱鍦ㄨ捀棣忚矾寰勫紩鍏ヤ换浣?LLM 璋冪敤
涓嶅緱鍦ㄤ换鍔″悓姝ラ摼璺笂鎵ц钂搁
```

### M4锛氭贩鍚堝彫鍥烇紙FTS5 + 鍚戦噺 + RRF锛夛紙6 澶╋級

褰撳墠鍙湁 FTS5 鍏抽敭璇嶅尮閰嶏紝"杩欎釜鏉愯川鍙傛暟涓轰粈涔堟槸杩欎釜鍊?杩欑被璇箟鏌ヨ鍙洖宸€?

**闆朵緷璧栧簳绾?*锛氬悜閲忚兘鍔涘仛鎴愬彲閫夛紝缂哄け鏃堕潤榛橀檷绾т负绾?FTS5銆?

```text
pyproject.toml
[project.optional-dependencies]
vector = ["sqlite-vec>=0.1,<1", "model2vec>=0.3,<1"]
```

宓屽叆妯″瀷閫夋嫨鍘熷垯锛?*鏁堢巼浼樺厛**锛夛細

```text
蹇呴』 CPU 鍙窇锛屾棤 GPU 渚濊禆
妯″瀷浣撶Н < 100 MB
鍗曟潯宓屽叆 < 10 ms
浼樺厛闈欐€佸祵鍏ワ紙model2vec 绫伙級锛屾嫆缁濋渶瑕佸畬鏁?transformer 鎺ㄧ悊鐨勬柟妗?
```

鐞嗙敱锛氳蹇嗘潯鐩槸鐭枃鏈紝闈欐€佸祵鍏ヨ川閲忚冻澶燂紝閫熷害蹇竴鍒颁袱涓暟閲忕骇銆傚畞鍙壓鐗插皯閲?
鍙洖璐ㄩ噺锛屼篃涓嶈兘璁╄捀棣忔垨鏌ヨ鍙樻參銆?

鏂板琛紙Schema 杩佺Щ v4 鈫?v5銆傝縼绉绘棤鏉′欢鎵ц骞跺缓琛紝鍗充娇 vector extra 鏈畨瑁咃紝
浠ヤ繚璇佺増鏈彿鏄叧浜庣粨鏋勭殑鍙潬闄堣堪锛夛細

```sql
CREATE TABLE memory_embeddings (
    record_id    TEXT PRIMARY KEY REFERENCES memory_records(record_id) ON DELETE CASCADE,
    model_id     TEXT NOT NULL,
    dim          INTEGER NOT NULL,
    embedding    BLOB NOT NULL,
    created_at_utc TEXT NOT NULL
);
```

鍙洖铻嶅悎锛圧RF锛屽弬鑰冨疄鐜板悓鎬濊矾锛夛細

```text
FTS5 BM25 top-k        鈫? rank_fts
鍚戦噺浣欏鸡 top-k          鈫? rank_vec
RRF: score = 危 1/(60 + rank)
鏈€缁堟寜 score 鎺掑簭锛屾柦鍔?2.2.1 鐨勪笁閲嶉绠?
```

宓屽叆鐢熸垚濂戠害锛?*璁板綍宓屽叆鍙湪 M3 钂搁鏃堕『甯︾敓鎴?*锛涙煡璇㈣矾寰勫彧涓烘煡璇㈡枃鏈敓鎴?1 娆?
宓屽叆锛屼笉閲嶇畻浠讳綍璁板綍宓屽叆銆傚悜閲忔绱㈠繀椤诲鏌ヨ姹傚祵鍏ユ墠鑳界畻鐩镐技搴︼紝鎵€浠?鏌ヨ鏃堕浂宓屽叆
鐢熸垚"鏄笉鍙弧瓒崇殑璇存硶锛屼笉寰椾綔涓洪獙鏀堕」銆傚彟闇€鎻愪緵鍙画璺戠殑鍥炲～鍛戒护锛屽鐞嗗悜閲忚兘鍔涘惎鐢?
鍓嶅凡瀛樺湪鐨?L1 璁板綍銆?

楠屾敹锛?

```text
[ ] 鏈畨瑁?vector extra 鏃讹紝鍏ㄩ儴鍔熻兘姝ｅ父锛岄€€鍖栦负 FTS5
[ ] 瀹夎鍚庯紝璇箟鏌ヨ鍙洖浼樹簬绾?FTS5锛堝噯澶?20 鏉″熀鍑嗘煡璇㈠姣旓級
[ ] 鍗曟娣峰悎鍙洖 < 300 ms锛堝惈棰勭畻鎴柇锛?
[ ] 鏌ヨ璺緞鍙敓鎴?1 涓?query embedding锛屼笉閲嶇畻 corpus / record embeddings
[ ] 宓屽叆妯″瀷缂哄け / 鍔犺浇澶辫触鏃堕潤榛橀檷绾э紝涓嶆姏寮傚父
```

### M5锛歀2 浠诲姟閰嶆柟 + L3 椤圭洰绾﹀畾锛? 澶╋級

L2/L3 鏄?*鍞竴浼氳娉ㄥ叆 prompt 鐨勫眰**锛屽洜姝ゅ繀椤绘瀬灏忋€佹瀬绋炽€?

**L2 Scenario锛堜换鍔￠厤鏂癸級**锛氭妸閲嶅鍑虹幇鐨勬垚鍔熸ā寮忚仛鍚堟垚"杩欑被浠诲姟杩欐牱鍋?銆?

```text
瑙﹀彂鏉′欢    鍚岀被鎿嶄綔锛堝悓 operation + 鍚岃祫浜х被锛夋垚鍔?鈮?3 娆?
浜у嚭        涓€鏉?鈮?200 瀛楃殑閰嶆柟鎽樿
鍐呭        鍏稿瀷 Plan 褰㈡€?+ 甯歌鎷掔粷鍘熷洜 + 蹇呴渶鍓嶇疆鏉′欢
```

**L3 Persona锛堥」鐩害瀹氾級**锛氶」鐩骇绋冲畾浜嬪疄銆?

```text
鍛藉悕瑙勮寖        浠庡疄闄呰祫浜ц矾寰勭粺璁℃帹瀵?
Policy 鍋忓ソ     浠庡疄闄?Policy 閰嶇疆鎻愬彇
楂橀閿欒        浠?knownIssue 鑱氬悎鍑?Top 3
```

L3 鎬婚噺纭笂闄愶細**鈮?400 Token**銆傝秴鍑烘椂鎸夊懡涓鐜囨窐姹般€?

娉ㄥ叆濂戠害锛堝叧閿紝鐩存帴鍐冲畾鏁堢巼锛夛細

```text
ue_memory_get_context 杩斿洖
  鈹溾攢 L3 椤圭洰绾﹀畾        鈮?400 Token   鎬绘槸杩斿洖锛堟瀬绋冲畾锛屽埄浜?KV-cache锛?
  鈹溾攢 L2 褰撳墠浠诲姟鍩熼厤鏂?  鈮?400 Token   浠呭懡涓椂杩斿洖锛屾渶澶?2 鏉?
  鈹斺攢 L1/L0             涓嶈繑鍥烇紝浠呭憡鐭?鍙敤 ue_memory_search 鏌ヨ"
```

L2/L3 鐢熸垚鍚屾牱绂荤嚎锛屼笌 M3 鍚屾壒娆℃墽琛屻€?

楠屾敹锛?

```text
[ ] L3 鈮?400 Token锛孡2 鍗曟潯 鈮?200 瀛楋紝鍚堣 鈮?800 Token
[ ] 娉ㄥ叆鍐呭鍦ㄦ棤鏂拌捀棣忔椂閫愬瓧鑺傜ǔ瀹氾紙淇濇姢 KV-cache锛?
[ ] L2 浠呭湪浠诲姟鍩熷懡涓椂鍑虹幇
[ ] 鍐峰惎鍔?/ 鏃犺蹇嗘椂杩斿洖绌哄瓧绗︿覆
[ ] 鍚姩娉ㄥ叆棰濆寤惰繜 < 200 ms
```

### M6锛氱鍙峰寲涓婁笅鏂囧帇缂╋紙4 澶╋紝鍙€?寤跺悗锛?

鍙傝€冨疄鐜扮敤 Mermaid 鍘嬬缉宸ュ叿鏃ュ織鐪?Token銆俇EAgentKit 鐨勫搴斿満鏅細

```text
Impact Analysis 鐨勬秷璐硅€呭浘    鈫?Mermaid graph
Change Set 鐨勬搷浣滃簭鍒?       鈫?Mermaid sequence
鐭ヨ瘑鏍戝眬閮ㄧ粨鏋?              鈫?Mermaid tree
```

**鍒ゅ畾涓哄彲閫?*锛氫粎褰?W5 鐪熷疄椤圭洰娴嬮噺鏄剧ず杩欎簺 JSON 纭疄鏄笂涓嬫枃鐡堕鏃舵墠瀹炴柦銆?
涓嶅仛鎶曟満浼樺寲銆?

### Track M 鎬昏

```text
M1 鏁堢巼鍩虹嚎涓庨绠楅棬绂?     3 澶?  鈫?蹇呴』鏈€鍏?
M2 L0 鑷姩鎹曡幏             4 澶?
M3 L0鈫扡1 瑙勫垯钂搁          5 澶?
M4 娣峰悎鍙洖                6 澶?
M5 L2/L3 涓庢敞鍏ュ绾?       5 澶?
M6 绗﹀彿鍖栧帇缂?             4 澶?  鈫?鍙€夛紝鏁版嵁椹卞姩
                    鍚堣 鈮?23-27 澶?
```

## 6. Track C 鈥?P4 鍗忎綔鎰熺煡

鍒嗘敮锛歚feature/source-control-p4`锛堟柊寤猴紝W4 鐨?C++ 鏀瑰姩钀藉湴鍚庡惎鍔級

### 6.0 绔嬪満

娌跨敤 ROADMAP 0.9 鐨勬棦瀹氬師鍒欙細**棣栫増鍙垎鏋愩€佹彁绀烘垨闃绘锛屼笉鑷姩鎶㈤攣鎴栬鐩栦粬浜轰慨鏀?*銆?
P4 鏄洟闃熷叡浜姸鎬侊紝璇搷浣滀唬浠疯繙楂樹簬鏈湴鍐欏叆锛屽洜姝ゆ瘮鐜版湁鍐欏叆闂ㄧ鏇翠繚瀹堛€?

### C1锛氬彧璇荤姸鎬佹劅鐭ワ紙4 澶╋級

C++ 渚ц蛋 UE 鍐呯疆 `ISourceControlModule`锛屼笉鐩存帴璋?`p4.exe`锛堥伩鍏嶅嚟鎹鐞嗕笌
宸ヤ綔鍖鸿В鏋愰棶棰橈紝UE 宸茬粡瑙ｅ喅杩囷級銆?

鏂板 EditorBridge handler锛?

```text
getSourceControlStatus
  杈撳叆  assetPaths[]锛堟湁鐣岋紝鈮?100锛?
  杈撳嚭  provider / enabled / 姣忚祫浜?
        { depotPath, checkedOut, checkedOutBy, locked, lockedBy,
          headRevision, haveRevision, isUpToDate, isAdded, isDeleted }
```

瀵瑰簲 MCP Tool锛?

```text
ue_get_source_control_status      鏈夌晫鎵归噺鏌ヨ
ue_get_asset_checkout_state       鍗曡祫浜х鍑?閿佸畾鐘舵€侊紙涓嶅仛璐ｄ换褰掑睘鎺ㄦ柇锛?
```

涓嶅紩鍏ユ柊渚濊禆锛屼笉鍋氫换浣?P4 鍐欐搷浣溿€?

楠屾敹锛?

```text
[ ] P4 鏈惎鐢?/ 鏈繛鎺ユ椂鏄庣‘杩斿洖 disabled锛屼笉鎶ラ敊銆佷笉鎸傝捣
[ ] 鏌ヨ 100 璧勪骇 < 2 s
[ ] 浠栦汉绛惧嚭 / 閿佸畾鐘舵€佹纭弽鏄?
[ ] 鍙锛岀粷涓嶈Е鍙?checkout
```

### C2锛氬啓鍏ュ墠鍐茬獊棰勬锛? 澶╋級

鎶?P4 鐘舵€佹帴鍏ユ棦鏈夊啓鍏ラ棬绂侀摼锛屼綔涓烘柊鐨?fail-closed 鏉′欢銆?

鎻掑叆浣嶇疆锛堝湪鐜版湁 Policy / Revision 鏍￠獙涔嬪悗锛孉pply 涔嬪墠锛夛細

```text
Plan 鈫?Policy 鈫?Revision 鈫?銆愭柊澧?P4 Preflight銆?鈫?Live Apply
```

棰勬瑙勫垯锛堝叏閮?fail-closed锛夛細

```text
浠栦汉閿佸畾           鈫?鎷掔粷锛屾姤 source-control-locked
浠栦汉绛惧嚭           鈫?鎷掔粷锛屾姤 source-control-checked-out-by-other
鏈湴闈炴渶鏂?        鈫?鎷掔粷锛屾姤 source-control-out-of-date
鏈鍑轰笖鍙       鈫?鎷掔粷锛屾姤 source-control-not-checked-out
                     锛堜笉鑷姩绛惧嚭锛岄渶鐢ㄦ埛鏄惧紡鎿嶄綔锛?
P4 涓嶅彲鐢?         鈫?鎸?Policy 閰嶇疆鍐冲畾 skip 鎴?fail
```

Policy 鏂板瀛楁锛?

```json
{
  "sourceControl": {
    "preflightEnabled": true,
    "requireCheckedOut": true,
    "requireUpToDate": true,
    "allowWhenProviderUnavailable": false,
    "autoCheckout": false
  }
}
```

`autoCheckout` 榛樿 `false` 涓?*棣栫増涓嶅疄鐜?true 鍒嗘敮**锛屼繚鐣欏瓧娈典互渚垮皢鏉ユ墿灞曘€?

楠屾敹锛?

```text
[ ] 鍏被鎷掔粷璺緞鍚勬湁鐪熷疄 P4 鐜楠岃瘉
[ ] 鎷掔粷鏃堕浂鍐欏叆銆侀浂 Dirty
[ ] P4 涓嶅彲鐢ㄤ笖 allowWhenProviderUnavailable=false 鏃舵嫆缁?
[ ] 棰勬棰濆寤惰繜 < 500 ms
[ ] 鏃㈡湁闈?P4 椤圭洰琛屼负瀹屽叏涓嶅彉锛堥粯璁ゅ叧闂級
```

### C3锛氬彉鏇村叧鑱斾笌瀹¤锛? 澶╋級

鎶?AI 淇敼鍏宠仈鍒?P4 Changelist锛屼緵浜哄伐 Review銆?

```text
ue_get_changelist_context     璇诲彇褰撳墠 pending changelist 鍙婂叾鏂囦欢
Change Set 鈫?Changelist       鍦?Change Set 璁板綍涓櫥璁?changelist 鍙?
Backup Manifest 鎵╁睍          澧炲姞 depotPath / headRevision 瀛楁
```

**涓嶅疄鐜拌嚜鍔?Submit**銆係ubmit 鏄笉鍙€嗙殑鍥㈤槦绾ф搷浣滐紝姘歌繙鐢变汉鎵ц銆?

楠屾敹锛?

```text
[ ] Change Set 鍙弽鏌ュ搴?P4 changelist
[ ] Backup Manifest 鍚?depot 淇℃伅
[ ] 鏃犱换浣曡嚜鍔?submit / revert 浠ｇ爜璺緞
```

### C4锛歁emory 鑱斿姩锛? 澶╋級

P4 鐘舵€佹槸楂樹环鍊?L0 浜嬩欢锛屾帴鍏?Track M锛?

```text
鍐茬獊鎷掔粷           鈫?knownIssue锛氭煇璧勪骇鍦ㄥ苟鍙戝啓鍏ヤ笅鏇捐閿佸畾鎷掔粷
绛惧嚭棰戞缁熻       鈫?projectFact锛氭煇鐩綍鐨勫彉鏇存椿璺冨害锛堜笉鍚汉鍛樻柇瑷€锛?
```

**浜哄憳褰掑睘杈圭晫**锛氳娴嬪埌鐨勭鍑?/ 閿佸畾鍘嗗彶鍙兘浣滀负浜嬪疄瑙傛祴瀛樺偍锛屼笉寰楄捀棣忎负
銆屾煇浜烘槸鏌愮洰褰曡礋璐ｄ汉銆嶈繖绫绘寔涔呮柇瑷€鈥斺€旈偅灞炰簬 model-inferred 缁撹锛屽瓨鎴?
tool-observed 浼氭薄鏌?source 鍒嗙骇锛屼笖浼氭妸璁板繂搴撳彉鎴愪釜浜烘椿鍔ㄨ褰曘€傝矗浠诲綊灞?
鍙厑璁告潵鑷」鐩厤缃殑鏄惧紡澹版槑銆佸洟闃熻鍒欐垨鐢ㄦ埛纭銆?

渚濊禆 Track M 鐨?M2 瀹屾垚銆?

### Track C 鎬昏

```text
C1 鍙鐘舵€佹劅鐭?       4 澶?
C2 鍐欏叆鍓嶅啿绐侀妫€      5 澶?
C3 鍙樻洿鍏宠仈涓庡璁?     3 澶?
C4 Memory 鑱斿姩         2 澶?
                鍚堣 鈮?14 澶?
```

## 7. Track V 鈥?鐭ヨ瘑搴?Web 鍙娴忚

鍒嗘敮锛歚feature/knowledge-web-view`锛堟柊寤猴紝鍙珛鍗充笌 W4 骞惰锛岄浂鍐茬獊锛?

### 7.0 绔嬪満锛堢敤鎴锋槑纭害鏉燂級

```text
浜哄伐涓嶅緱鐩存帴淇敼鐭ヨ瘑搴擄紝鍐欏叆鍙兘鐢?Agent 瀹屾垚
```

鍥犳 Web 鐣岄潰鏄?*涓ユ牸鍙**鐨勩€傝繖涓嶆槸闃舵鎬уΕ鍗忥紝鑰屾槸姘镐箙鏋舵瀯绾︽潫锛?

```text
鍚庣鍙紑鏀?GET锛屼笉瀹炵幇浠讳綍 POST / PUT / DELETE
鏁版嵁搴撹繛鎺ヤ互鍙妯″紡鎵撳紑锛圫QLite mode=ro锛?
闇€瑕佷慨鏀规椂锛岀晫闈㈡彁绀?璇疯 Agent 鎵ц"锛屽苟缁欏嚭寤鸿鐨?Agent 鎸囦护鏂囨湰
```

杩欎釜绾︽潫鍙嶈€岀畝鍖栦簡瀹炵幇锛氭棤闇€閴存潈鍐欏叆銆佹棤闇€骞跺彂鎺у埗銆佹棤闇€浜嬪姟鍐茬獊澶勭悊銆?

### V1锛氭湰鍦板彧璇绘祻瑙堝櫒锛? 澶╋級

鎶€鏈€夊瀷锛?*闆舵柊澧炶繍琛屾椂渚濊禆**锛夛細

```text
鍚庣    Python 鏍囧噯搴?http.server + sqlite3锛坢ode=ro锛?
        涓嶅紩鍏?fastapi / uvicorn / starlette
鍓嶇    鍗曚釜闈欐€?HTML + 鍘熺敓 JS锛屾棤鏋勫缓姝ラ銆佹棤 npm
鍚姩    ue-agent knowledge-view --port 8765
缁戝畾    浠?127.0.0.1锛屼笉鐩戝惉澶栭儴鎺ュ彛
```

閫夋爣鍑嗗簱鑰岄潪 FastAPI 鐨勭悊鐢憋細椤圭洰褰撳墠 `dependencies = []`锛學eb 娴忚鏄緟鍔╁姛鑳斤紝
涓嶅€煎緱涓哄畠寮曞叆 ASGI 鏍堜笌杩愯鏃朵緷璧栥€傚彧璇?JSON 鎺ュ彛鐢?`http.server` 瀹屽叏澶熺敤銆?

**瀹夊叏璇存槑**锛氱粦瀹?127.0.0.1 涓斿彧璇伙紝浣嗕粛闇€鍦ㄦ枃妗ｄ腑鏄庣‘杩欐槸鏈湴寮€鍙戝伐鍏凤紝
涓嶅簲鏆撮湶鍒扮綉缁溿€傞粯璁や笉鍚敤浠讳綍閴存潈锛堟湰鍦板彧璇汇€佹棤鍐欏叆闈級銆?

椤甸潰锛堝洓涓鍥撅級锛?

```text
鐭ヨ瘑鏍?     宸︽爲 + 鍙宠鎯咃紝灞曞紑鑺傜偣鐪嬫寕杞界殑璁板綍
璁板綍鍒楄〃    鎸?type / status / source 绛涢€夛紝鏄剧ず stale 鏍囪
Active Work 褰撳墠鐩爣 / TODO / 闃诲 / 涓嬩竴姝?
Evidence    鍗曟潯璁板綍鐨勮瘉鎹摼锛屾寚鍚?receipt / checkpoint / diff
```

鍙 API锛?

```text
GET /api/tree                    鐭ヨ瘑鏍戠粨鏋?
GET /api/node/<node_id>          鑺傜偣璇︽儏 + 鎸傝浇璁板綍
GET /api/records?type=&status=   璁板綍鍒楄〃锛堝垎椤碉級
GET /api/record/<record_id>      璁板綍璇︽儏 + Evidence
GET /api/work                    Active Work
GET /api/status                  Memory 鐘舵€佹憳瑕?
```

楠屾敹锛?

```text
[ ] 鏁版嵁搴撲互 mode=ro 鎵撳紑锛屽啓鍏ュ皾璇曞湪浠ｇ爜灞備笉瀛樺湪
[ ] 浠呯洃鍚?127.0.0.1
[ ] 闆舵柊澧炶繍琛屾椂渚濊禆锛坧yproject dependencies 淇濇寔 []锛?
[ ] 鏃?npm / 鏋勫缓姝ラ
[ ] Memory 鏁版嵁搴撲笉瀛樺湪鏃剁粰鍑烘竻鏅版彁绀鸿€岄潪宕╂簝
[ ] stale / conflicted / superseded 鐘舵€佹湁鏄庣‘瑙嗚鍖哄垎
[ ] 鐣岄潰浠讳綍浣嶇疆閮戒笉鎻愪緵缂栬緫鍏ュ彛
```

### V2锛氬彲瑙嗗寲鍒嗘瀽闈㈡澘锛? 澶╋紝V1 涔嬪悗锛?

鍦ㄥ彧璇诲墠鎻愪笅澧炲姞鍒嗘瀽瑙嗗浘锛?

```text
璧勪骇寮曠敤鍥?     Asset 鈫?Asset 渚濊禆锛屽姏瀵煎悜鍥撅紝鍙笅閽?
褰卞搷鑼冨洿鍥?     閫変腑璧勪骇锛岄珮浜叾娑堣垂鑰咃紙澶嶇敤 Impact Analysis 鏁版嵁锛?
鐭ヨ瘑瑕嗙洊鐑浘    鍝簺璧勪骇鐩綍鏈夎蹇嗐€佸摢浜涙槸鐩插尯
鍙樻洿鏃堕棿绾?     Change Set 鏃跺簭 + Trust 缁撴灉
stale 鍒嗗竷      鍝簺鐭ヨ瘑鍥犺祫浜у彉鏇村け鏁堬紝鎸夌洰褰曡仛鍚?
```

鍥惧舰搴撻€夋嫨锛氬崟鏂囦欢鍙?vendored 鐨勮交閲忓簱锛堝 d3 鍗曟枃浠舵瀯寤猴級锛屼笉寮曞叆 npm銆?

楠屾敹锛?

```text
[ ] 5000 鑺傜偣寮曠敤鍥惧彲浜や簰锛堜笉鍗℃锛?
[ ] 鍥炬暟鎹叏閮ㄦ潵鑷幇鏈?SQLite锛屼笉鏂板瀵煎嚭姝ラ
[ ] 浠嶇劧涓ユ牸鍙
```

### Track V 鎬昏

```text
V1 鏈湴鍙娴忚鍣?       6 澶?
V2 鍙鍖栧垎鏋愰潰鏉?       8 澶?
                  鍚堣 鈮?14 澶?
```

## 8. 妯悜锛氱淮鎶ゆ€т笌鎶€鏈€?

杩欎簺涓嶅崟鐙崰 Track锛屾彃鍏ュ埌鍚?Track 鐨勮嚜鐒堕棿闅欍€?

### D1锛氭媶鍒?agent_workflow.py锛? 澶╋紝W4 涔嬪悗绔嬪嵆锛?

5,454 琛屽凡鏄淮鎶ょ儹鐐癸紝W4 浼氬啀寰€閲屽姞缂栨帓閫昏緫銆俉4 瀹屾垚鍚庡繀椤绘媶鍒嗭細

```text
agent_workflow.py  鈫? workflow_plan.py       Plan / DryRun
                      workflow_live.py       Live Apply / Undo / Discard
                      workflow_verify.py     Save / Verify / Checkpoint
                      workflow_batch.py      W4 鎵归噺缂栨帓
                      workflow_common.py     鍏辩敤绫诲瀷涓庤矾寰?
```

绾︽潫锛氱函绉诲姩 + import 璋冩暣锛屼笉鏀硅涓猴紱`test_tool_registry.py` 淇濊瘉宸ュ叿闈笉鍙樸€?

**鏃舵満寰堝叧閿?*锛氬繀椤诲湪 W4 涔嬪悗銆乀rack M 瀹炵幇涔嬪墠銆俉4 涔嬪墠鎷嗕細涓?W4 鍐茬獊锛?
Track M 涔嬪悗鎷嗕細鐗靛姩鏇村璋冪敤鐐广€?

### D2锛歍ool 璁℃暟鍗曚竴鏉ユ簮锛? 澶╋級

褰撳墠 105 / 93 / 60 绛夎鏁版暎钀藉湪 README銆丷OADMAP銆佹祴璇曚腑锛屽鏄撲笉涓€鑷淬€傛敼涓轰粠
娉ㄥ唽琛ㄨ繍琛屾椂瀵煎嚭锛屾枃妗ｅ紩鐢ㄧ敓鎴愮粨鏋溿€?

### D3锛歎E Build CI锛? 澶╋級

褰撳墠 UE5.6 缂栬瘧鍙湪鏈湴鍙戝竷鏈烘墽琛屻€傜洰鏍囨槸鍦ㄦ湁寮曟搸鐜鐨勬満鍣ㄤ笂鍋氬畾鏃剁紪璇戦棬绂侊紝
閬垮厤 C++ 鏀瑰姩绱Н鍚庢墠鍙戠幇闂銆俆rack C 浼氬姞 C++ 浠ｇ爜锛孌3 鏈€濂藉湪鍏朵箣鍓嶃€?

### D4锛欰PI 鍙傝€冩枃妗ｏ紙2 澶╋級

琛?0.1 鑺傚垎鏋愬嚭鐨勬枃妗ｇ己鍙ｏ細浠?MCP 娉ㄥ唽琛ㄨ嚜鍔ㄧ敓鎴愬伐鍏峰弬鑰冿紝鎸夊満鏅垎缁勩€?
鍙笌 Track V 鍚堝苟浜や粯锛圵eb 鐣岄潰椤哄甫鎻愪緵宸ュ叿娴忚椤碉級銆?

## 9. 鎬绘帓鏈熶笌渚濊禆鍥?

```text
T0  W3 鏀跺彛 checkpoint                 complete   45e6ea2
     鈹?
     鈹溾攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
     鈻?                                                鈻?
W4  澶氭搷浣?/ 鏈夌晫鎵归噺        24 澶?         V1 鍙娴忚鍣?     6 澶?
     鈹?                                                鈹?
     鈹? 锛圕hange Set 缁撴瀯鍐荤粨锛?                         鈻?
     鈹?                                     V2 鍙鍖栭潰鏉?    8 澶?
     鈻?
D1  鎷嗗垎 agent_workflow      3 澶?
     鈹?
     鈹溾攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?
     鈻?                 鈻?                 鈻?
W5  鐪熷疄椤圭洰楠屾敹 10 澶?  M1 鏁堢巼鍩虹嚎 3 澶?   C1 P4 鍙 4 澶?
                        鈹?                 鈹?
                        鈻?                 鈻?
                   M2 L0 鎹曡幏 4 澶?     C2 鍐茬獊棰勬 5 澶?
                        鈹?                 鈹?
                        鈻?                 鈻?
                   M3 L1 钂搁 5 澶?     C3 鍙樻洿瀹¤ 3 澶?
                        鈹?                 鈹?
                        鈻?                 鈹?
                   M4 娣峰悎鍙洖 6 澶?        鈹?
                        鈹?                 鈹?
                        鈻?                 鈹?
                   M5 L2/L3 娉ㄥ叆 5 澶?鈼勨攢鈹€鈹€鈹€鈹€鈹?
                        鈹?             C4 Memory 鑱斿姩 2 澶?
                        鈻?
                   M6 绗﹀彿鍖栧帇缂?4 澶╋紙鍙€夛級
```

### 鍏抽敭渚濊禆

```text
W4 鈫?D1        W4 鍔犲畬缂栨帓鍐嶆媶鍒嗭紝閬垮厤鍐茬獊
W4 鈫?M2        L0 钂搁婧愬繀椤诲厛鍐荤粨
D1 鈫?M/C       鍦ㄦ媶鍒嗗悗鐨勬ā鍧椾笂寮€鍙戯紝閬垮厤澶ф枃浠跺啿绐?
M2 鈫?C4        P4 浜嬩欢闇€瑕?L0 閫氶亾
V1/V2 鐙珛     鍏ㄧ▼鍙苟琛?
```

### 閲岀▼纰?

```text
閲岀▼纰?1锛堚増4 鍛級   W4 瀹屾垚 + V1 涓婄嚎
                    鈫?Agent 鍙仛澶氭搷浣滀换鍔★紝鐭ヨ瘑搴撳彲瑙?
閲岀▼纰?2锛堚増7 鍛級   D1 + M1-M3 + C1 瀹屾垚
                    鈫?璁板繂寮€濮嬭嚜鍔ㄧН绱紝P4 鐘舵€佸彲瑙?
閲岀▼纰?3锛堚増11 鍛級  M4-M5 + C2-C3 + V2 瀹屾垚
                    鈫?娣峰悎鍙洖鍙敤锛屽崗浣滃畨鍏紝鍒嗘瀽闈㈡澘鍙敤
閲岀▼纰?4            W5 瑙勬ā楠屾敹 + 0.9 鍙戝竷璇勫
```

鎸夊崟浜?AI 杈呭姪寮€鍙戜及绠楋紝鎬婚噺绾?11鈥?3 鍛ㄣ€傚洓鏉?Track 骞惰涓嶆剰鍛崇潃浜哄姏骞惰锛岃€屾槸
**閬囧埌闃诲鏃跺彲鍒囨崲鍒板彟涓€鏉＄嚎锛屼笉绌虹瓑**鈥斺€旇繖涔熸槸鐢ㄥ worktree 鐨勫疄闄呮敹鐩娿€?

## 10. 鍏ㄥ眬楠屾敹闂ㄧ

娌跨敤鐜版湁闂ㄧ锛屾柊澧炰笁椤癸細

```text
鏃㈡湁
[ ] python scripts\ValidateRelease.py --require-release-docs
[ ] Ruff / 瀹屾暣 Python suite / compileall
[ ] UE5.6 Direct Build锛堣Е纰?C++ 鏃讹級
[ ] UTF-8 鏃?BOM / CRLF / whitespace / 瀹屾暣 diff

鏂板
[ ] pyproject dependencies 淇濇寔 []锛堝悜閲忚兘鍔涗粎鍦?optional 鍐咃級
[ ] Memory 寮€閿€闂ㄧ锛歴cripts\MeasureMemoryOverhead.py 鍏ㄩ儴杈炬爣
[ ] Web 瑙嗗浘鍙鏂█锛氭棤浠讳綍鍐欏叆 SQL 璺緞
```

## 11. 椋庨櫓涓庡绛?

| 椋庨櫓 | 褰卞搷 | 瀵圭瓥 |
|---|---|---|
| Memory 鎷栨參浠诲姟锛堢敤鎴峰凡閬囧埌杩囷級 | 宸ュ叿鍙樺緱涓嶅彲鐢?| M1 鍏堝缓闂ㄧ锛涗换涓€鎸囨爣瓒呮爣鍗?blocked锛涜捀棣忛浂 LLM銆佸叏寮傛 |
| W4 鎵归噺鏀惧ぇ recovery 澶嶆潅搴?| 閮ㄥ垎搴旂敤鐘舵€侀毦鎭㈠ | 娌跨敤 W4 璁″垝鐨勬樉寮?partial 杈圭晫锛涗笉澹版槑璺ㄥ寘鍘熷瓙鎬?|
| 鍚戦噺渚濊禆鐮村潖闆朵緷璧栧簳绾?| 瀹夎澶嶆潅鍖?| 鏀惧叆 optional-dependencies锛岀己澶遍潤榛橀檷绾?|
| P4 璇搷浣滃奖鍝嶅洟闃?| 浠ｄ环楂樹簬鏈湴鍐欏叆 | 棣栫増绾彧璇?+ fail-closed 棰勬锛涙棤鑷姩 submit/checkout |
| agent_workflow.py 缁х画鑶ㄨ儉 | 缁存姢鎴愭湰澶辨帶 | D1 鍦?W4 鍚庡己鍒舵墽琛岋紝涓嶅彲寤跺悗 |
| 鍥?Track 骞惰鍐茬獊 | 鍚堝苟鍥伴毦 | 鎸夌 3.1 鑺傛帓鏈燂紱C 绛?W4 鐨?C++ 钀藉湴锛沄 鍏ㄧ▼鐙珛 |
| 鑷姩璁板繂鍐欏叆鍣０ | 鐭ヨ瘑搴撴薄鏌?| 鍙敤 tool-observed锛涘幓閲嶉潬 UNIQUE 绾︽潫锛泂tale 鑷姩澶辨晥 |

## 12. 绔嬪嵆鍙墽琛岀殑涓嬩竴姝?

```text
1. 鎸?W4 璇︾粏璁″垝鍚姩 W4-0锛圕ontract Freeze and Baseline锛?
2. W4 鍐呴儴闃舵銆佸け璐ヨ涔変笌 C1-C12 鍧囦互 W4 Detailed Plan 涓哄敮涓€鏉冨▉
3. 鍏朵粬 Track 浠呭湪闇€瑕佸垏鎹富绾挎垨婊¤冻鍏跺墠缃潯浠舵椂鍚姩锛涗笉褰卞搷褰撳墠 W4 涓荤嚎
```

Track M 鐨勫疄鐜颁笉瑕佸湪 W4 瀹屾垚鍓嶅紑濮嬧€斺€擫0 钂搁婧愪細鍙樸€備絾璁捐鏂囨。鍙互鐜板湪鍐欙紝
杩欐牱 W4 涓€缁撴潫灏辫兘鐩存帴杩涘叆 M1銆?

