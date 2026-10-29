# Bot ladder

Five single-file bots of increasing strength. Practice against them locally, beat each level,
then enter the real tournament. Each file only needs `pokbot_core` (included with the CLI).

| Level | Bot | Style | How to beat it |
|---|---|---|---|
| 1 | `l1_fish.py` | calls almost anything | bet good hands for value; never bluff |
| 2 | `l2_maniac.py` | raises and bets nearly every hand | call down with decent hands |
| 3 | `l3_rock.py` | premium hands only | steal its blinds; fold when it bets |
| 4 | `l4_tag.py` | position-aware, pot odds, draws, c-bets | pressure its one-pair hands |
| 5 | `l5_shark.py` | Monte Carlo equity, sized bets, adaptive bluffs (~1.2 ms/decision) | you tell us |

```
pokbot play mybot.py bots/ladder/l3_rock.py --local     # your bot vs level 3 (+ [BOT]s)
uv run python bots/ladder/bench.py                      # re-rank the ladder
```

Heads-up over 2,000 duplicate hands (seed 7), every level beats every level below it:

| bb/100 for row | maniac | rock | tag | shark |
|---|---|---|---|---|
| fish | -406 | -312 | -149 | -358 |
| maniac | | -124 | -164 | -1219 |
| rock | | | -21 | -6 |
| tag | | | | -89 |
