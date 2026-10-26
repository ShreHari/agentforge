# Devlog

One or two lines a day: what works now that didn't yesterday, and what's next.

## 12 Oct

- Project set up with uv; Postgres 16 running in Docker.

## 18 Oct

- Baseline added: 5-step LangGraph graph (fake steps), Postgres checkpoints, crash and
  resume from the terminal, api skeleton, 22 tests.
- Next:
## 21 Oct:
- Fixed the postgres port clash( mac posstgres on 5432)

## 25 OCT :
- Real Ai match has been done : 62 (keywords) to 69 haha (gemini): 23 test pass
- the draft has worked!!Even though the prompt says that dont invent!!it invented 3 of 5 bullets,got to eval now!
- the 15 test cases has been passed ( 5 clean , 5 messy , 5 adversarial) on two runs!! , and the prompt changes took it from the 0/6 to 6/6 , and the new cases added honesty, injection and messy - input coverage.
 - first deep agent : it planned with write to_dos,it chose with own 2 searches,since it dosent know todays date it just gave 2024 2025
 - Research is now a real Deep Agent: web search via Tavily, 3-search limit in code, today’s date in the prompt, invented URLs dropped. First full real run: for a made-up company it said ‘not found’ instead of guessing
 - CV upload: /parse turns PDF or Word into text, with size, page, password and zip-bomb limits. Measured 2 PDF libraries on a real CV and switched to pdfplumber (x_tolerance=1.5): glued words 64 → 0. A test locks the setting.

 # 26th october :
 - Pub?Suph (emulator locally) : the api publishes run id,the worker acts only after saving,the sweeper re queus stuck runs and failed poison runs after three tries.test s move to thier own data base so they can never trigger real ai runs. 84 test passes!.