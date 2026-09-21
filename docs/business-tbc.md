# Business TBC — must confirm before real-money use (config.confirmed=False until then)

From BRD/SRS §§3,11–12,14 (Game 3 + platform), all live as flags in
`games/teen_patti_pro/config.py` (`tbc` tuple, stamped per settlement):
1. G3-BR-01 variant/ranking (default: standard trail>pure-seq>seq>color>pair>high).
2. G3-BR-02 3 cards/position (default: 3, single 52-deck).
3. G3-BR-03 pot/payout/tie (default: dead-heat pro-rata, dust carried, rake 0).
4. G3-BR-04 timer (default: guess_ms 20000).
5. G3-BR-05-MECH RNG mechanism (default: server CSPRNG seed + sha256 deck commitment).
6. DENOMS (default 20/100/500/1000), min 20 / max 100000.
7. SEATS fixed A/B/C. 8. TIE-REMAINDER carry-over.
9. Final branding/game name. 10. Admin RBAC matrix sign-off. 11. Auth/wallet source +
    currency model [CLIENT API REQUIRED]. 12. WebView viewport/sizes/orientation.
13. Leadercc vendor contract reuse (same-Redis token policy). 14. Teen Patti gameplay
    video/screenshots (missing — needed for pixel-faithful UI).
