# Horse in the Hospital: production bible

The build session reads this first. Every decision below was made on purpose; change one only if the
footage proves it wrong, and note the change at the bottom.

## The goal

A ~3:30 music video that honors John Mulaney's "Horse in a Hospital" bit. Joel will share it with a few
people as a comedy piece and as a showpiece. It has to be funny on first watch, look expensive, and land
one honest beat without preaching.

The thesis, never stated out loud: nobody knows what the horse will do next, least of all the horse, and
the rest of us are standing in the waiting room watching the horse on TV while the actual horse is
right behind us eating the ficus. The mirror points at everyone: the brunch people, the people who
opened the door, and the narrator himself ("I wasn't paying attention").

## Tone rules

1. Play it completely straight. Prestige medical drama, shot like a serious film. The horse is the only
   absurd thing in frame, and nobody in the film acts like it's a bit. Deadpan is the engine.
2. Never decode the metaphor. No orange hair, no red ties, no flags, no politicians' faces. The horse is a
   horse, the hippo is a hippo. The bit never names anyone beyond "this guy," and neither do we.
3. No real people's likenesses (Mulaney, Busey, Andy Cohen, anyone). Names may appear in sung lyrics only.
4. No harm shown. The horse charges toward the NICU and is stopped by a nurse with a crash cart; the hippo
   threatens and yawns. Chaos is paperwork, IV poles, hay, and staff morale.
5. Cable-news bleeps are the default cut (in-world, funnier, shareable with anyone). Also deliver an
   uncensored cut.
6. Cut on purpose: the N-word line, the new-Nazis closer. Sleepover kid is the post-credits stinger.
7. Lyrics on screen only for choruses and emotional lines (Joel's taste), serif italic. Cable-news chyrons
   and the ticker carry the other text jokes.

## Length and structure

Target 3:30, hard cap 3:50. Suno is the spine (~3:30); my inserts add ~15 s; trim in post if anything sags
(brunch first, then the outro).

| Section | What happens | Audio source |
| --- | --- | --- |
| Intro | Night exterior, St. Eligius Memorial, rain. Empty corridor, one hoof print in spilled coffee. Title card. | Suno |
| Verse 1 | Waiting room: everyone furious at screens. "Sticky": a shoe stuck to the floor. "It's like..." elevator doors | Suno |
| Chorus 1 | Doors open: THE HORSE. Slow-mo majesty under fluorescents. Close on the confused eye. | Suno |
| Verse 2 | Cable news split screen: anchor + the bird-in-airport expert at a gate. | Suno + news sting SFX |
| Verse 3 | Staff around the nurses' station TV. Horse rides the elevator, calm. Floor indicator. | Suno + elevator ding |
| Break | Operating room freezes. Heart monitor. Shadow passes the frosted door. | Suno, then INSERT 2-4 bars of hoof clops on linoleum over room tone |
| Verse 4 | Horse peacefully eating a get-well bouquet. Staff exhale. Chyron: HORSE CALM, SOURCES SAY | Suno |
| Drop | Horse rears, gallops toward double doors marked NICU. Nurse slams a crash cart across the doors. | Suno + gallop SFX |
| Stop-time | Horse skids to a halt in a drift of paperwork, looks at camera. Bleep over the word, black bar on the lyric. | Suno + bleep |
| Verse 5 | Overexposed brunch, linen, mimosas. "We're well past that": horse in the cafeteria line with a tray. | Suno |
| Verse 6 | Globe graphic, dotted line 5,000 miles to a mud lake. Hippo half-submerged, staring. | Suno |
| Instrumental | HORSE vs HIPPO, wrestling-promo split screen with stat chyrons. | Suno stems + INSERT ElevenLabs Horse and Hippo promo (extend bars if needed) |
| "Okay." | The waiting room, deflated, blank. One man slowly sips coffee. The Nurse looks at camera. | Suno |
| Verse 7 | Horse-catcher's heroic slow walk with a lasso and lanyard; then carrying a cardboard box out past security. A colleague in a plain tricorn hat looks confused. | Suno |
| Bridge | THE MIRROR: everyone glued to screens showing the horse; the horse behind them eating the ficus. Automatic door "WELCOME". "NOW SERVING 4" ticket machine. Nanny cam. | Suno |
| Silence | After "Gary Busey..." one bar of black and a single hoof clop. | Suno silence + clop |
| Final Chorus | Key change. Atrium parade: staff line the balconies, paperwork confetti, sunrise. Everything converges. "Neither do your parents": elderly couple in gowns, synchronized shrug. | Suno |
| Outro | Dawn. Horse alone at a window, looking out. Turns to camera. End card with credit. | Suno |
| Stinger | Black. Clock 11:59 to 12:00. Kid: "It's tomorrow now." (or: horse in elevator, doors close, muzak chorus, ding) | ElevenLabs |

End card: "After 'Horse in a Hospital' by John Mulaney (Kid Gorgeous at Radio City, 2018)."

## Recurring cast (keep these descriptions verbatim in every image prompt)

- **The Horse**: a large chestnut thoroughbred with a white blaze down its face, four white socks, a dark
  mane, and a blue hospital patient wristband around its left foreleg printed "HORSE". Expression: mild,
  sincere confusion. Make a reference sheet first and pass it to every generation.
- **The Nurse** (audience surrogate, our Jim-from-The-Office): night-shift charge nurse in her fifties,
  navy scrubs, reading glasses on a beaded chain, grey-streaked bun, unflappable. Her looks to camera are
  the laughs. She is the "Okay."
- **The Hippo**: an enormous grey-pink hippopotamus half-submerged in a brown lake of mud, scarred snout,
  tiny twitching ears. Only ever seen on screens, in grainy long-lens "foreign feed" footage.
- **The Bird Man** (expert): earnest man in a fleece vest at an airport gate, pigeon on the departures sign.
- **The Horse-catcher**: rugged man with a coiled lasso and a hospital ID lanyard, Armageddon walk.

Setting: St. Eligius Memorial (patron saint of horses; also the hospital in St. Elsewhere).

## Look

Photoreal, prestige drama: shot on ARRI Alexa, anamorphic 2.39:1, cool teal fluorescent light against
warm practicals, shallow depth of field, 35 mm grain, motivated lighting, symmetrical corridors. The
cable-news layer is the opposite: flat, bright, chyron red and white, rendered by me in code (crisp text,
no AI lettering). Lyrics: serif italic, bottom third, choruses and emotional lines only.

## Audio plan

- Suno keeper -> stems (Demucs: vocals, drums, bass, other) so I can mute the band for the break, duck
  under dialogue, and strip any vocals Suno puts on the instrumental break.
- Inserts cut on downbeats from `analysis.json`; extend the instrumental break by repeating bars if the
  promo dialogue needs more room.
- ElevenLabs v3 voices (designed, never cloned from a real person): Anchor, Bird Man, Horse (unhinged
  wrestling-promo growl), Hippo (huge, wet, slow; pitch down slightly), Hospital PA, Sleepover Kid.
  Joel may supply his own cloned voice ID for the Bird Man cameo.
- ElevenLabs sound effects: hooves on linoleum (walk, trot, gallop), elevator ding and doors, heart
  monitor beeps in tempo, whinny, snort, hippo bellow, mud bubbles, crowd gasp, news sting, PA chime,
  surgical tray clatter.
- Bleep: 1 kHz sine, generated locally. Two masters: bleeped and uncensored.
- Every TTS line checked with Whisper before it goes in. Master to -14 LUFS, true peak -1 dBTP.

## Picture plan

- Stills: Gemini image model (Nano Banana) with the Horse and Nurse reference sheets for consistency.
- Motion: image-to-video on the shots that need it (gallops, elevator doors, the rear-up, the atrium
  parade, the hippo yawn). Deadpan shots can stay stills with slow push-ins; stillness is part of the joke.
- Chyrons, ticker, wrestling VS card, globe graphic, end card: my own motion graphics (Playwright canvas or
  PIL), driven by the song timing.
- Shots cut on bar lines; ~45-55 shots.

Chyron and ticker copy bank (use, cut, add):
- BREAKING: HORSE LOOSE IN HOSPITAL
- EXPERT: MAN WHO ONCE SAW BIRD IN AIRPORT
- UPDATE: HORSE USED ELEVATOR
- HORSE CALM, SOURCES SAY
- DEVELOPING: HORSE NO LONGER CALM
- HIPPO CLAIMS LAKE OF MUD MAKES HIM "CRAZY"
- HORSE FIRES HORSE-CATCHER
- BRUNCH ATTENDEES: "THERE SHOULDN'T BE A HORSE IN THE HOSPITAL"
- JUST 'CAUSE YOU'RE ACCURATE DOESN'T MEAN YOU'RE INTERESTING
- HOSPITAL WAS "INEFFICIENT," SAYS MAN WHO OPENED DOOR
- HORSE "AS CONFUSED AS YOU ARE"
- DAY 1,4xx OF HORSE

## Reference sheets (make these first, then pass them as `refs` on every shot that shows the character)

```
songvid image horse-hospital --out refs/horse.png --prompt "Character reference photograph. A large chestnut thoroughbred horse with a white blaze down its face, four white socks, a dark mane and tail, and a blue hospital patient wristband around its left foreleg printed HORSE. Standing three-quarters to camera in an empty hospital corridor at night under fluorescent light. Mild, sincere confusion in its eyes. Photorealistic, shot on ARRI Alexa, 40mm anamorphic, shallow depth of field, 35mm film grain."
songvid image horse-hospital --out refs/horse_face.png --ref refs/horse.png --prompt "Same horse, extreme close-up of its face and eye, three-quarter view, the white blaze clear, fluorescent light reflected in the eye, mild sincere confusion. Photorealistic, cinematic, shallow depth of field."
songvid image horse-hospital --out refs/nurse.png --prompt "Character reference photograph. A night-shift charge nurse in her fifties in navy scrubs, reading glasses on a beaded chain, grey-streaked hair in a bun, unflappable deadpan expression, standing at a hospital nurses' station at 3 a.m. Photorealistic, cinematic, motivated practical light, 35mm film grain."
songvid image horse-hospital --out refs/hippo.png --prompt "An enormous grey-pink hippopotamus half-submerged in a brown lake of thick mud, scarred snout, tiny ears, staring straight into the lens. Long-lens telephoto news footage, slightly grainy, overcast light."
songvid image horse-hospital --out refs/corridor.png --prompt "Look reference: a symmetrical hospital corridor at 3 a.m., teal fluorescent light against warm practical lamps at a nurses' station, polished linoleum floor, wayfinding signs reading ST. ELIGIUS MEMORIAL. Empty. Photorealistic, prestige-drama cinematography, anamorphic, 35mm grain."
```
Look at every sheet before using it. If the horse isn't right, fix the sheet; everything downstream inherits it.

## Runbook

1. Keys: `python -c "from songvid import keys; [print(k, keys.test(k)) for k in ['ELEVENLABS_API_KEY','GEMINI_API_KEY','FAL_KEY']]"`
   (prints pass/fail only, never values). Then probe Gemini's image model list and fix `gemini_image_model`
   in songvid.yaml if `gemini-nano-banana-2.1` isn't the current id.
2. Song: `songvid suno horse-hospital --import <keeper.wav> --url <suno link>`, then
   `songvid align horse-hospital --method whisper` (Demucs first, per songvid.yaml) and `songvid analyze horse-hospital`.
   Check `lyrics.srt` line starts against the vocal stem's energy; fix by hand if a line is off.
3. Voices: `songvid voices horse-hospital` (designs and saves the 7 voices, then all line and SFX takes).
   QA every line take with Whisper (does it say the words?) and pick takes by fit: duration for the slot,
   energy for shouted lines. Record picks in mix.json.
4. Mix: write mix.json against analysis.json downbeats (source time):
   - insert ~8 s at 0.0 for the cold open: news_sting + anchor_open (fx tv), then the piano.
   - stop-time insert, 2 bars, right after "Who once saw a bird in the airport": bird_man (fx phone), so
     "Get outta here with that!" lands as a reply.
   - insert 2 bars after "has anyone heard...": room_tone + hooves_walk (fx hall). Silence is the joke.
   - elevator_ding right after "(The horse used the elevator?)".
   - Instrumental Break: mute any Suno vocals there; extend with "repeat" bars until hippo_bomb,
     horse_promo, hippo_crazy fit with breaths (hippo pitch -2, fx none; bleeps "auto").
   - pa_catcher (fx pa, duck -8) right after "I have fired the horse-catcher!"
   - bleeps: [{"word": "fucking"}] on the sung stop-time line.
   - stinger after the song ends: clock_midnight, kid_tomorrow, beat, nurse_sleepingbag, elevator_ding.
   Then `songvid mix horse-hospital`. Check both masters' loudness and that every insert lands on a downbeat.
5. Picture: reference sheets (above), then storyboard.json by hand: ~45-55 shots on bar lines from the
   *mixed* analysis.json, source "generated", refs per character, video_model per shot (veo-fast default;
   seedance only for 2-3 hero shots), letterbox false for TV shots, overlays for chyrons/ticker/bug/VS/cards,
   censor ["fucking"], lyrics_overlay only on choruses and emotional lines.
6. `songvid keyframes horse-hospital` -> `songvid stills horse-hospital` -> look at the contact sheet hard.
   Redo weak stills (`songvid approve <ids> --reject --note ...` then `keyframes --redo`).
7. `songvid approve horse-hospital all` -> `songvid animate horse-hospital` (dry run prints cost) ->
   `--yes` only within the cap. Deadpan shots can stay stills.
8. `songvid render horse-hospital --preview --start <chorus> --end <chorus+30>`; watch it (frames) and fix.
   Then full `songvid render horse-hospital` and `songvid render horse-hospital --uncensored`.
9. Deliver: a share encode (H.264, CRF ~22, AAC 256k, faststart) of both cuts, sent to Joel.

## Tooling built for this (all in songvid, all tested)

- `songvid mix`: inserts/cuts with re-timed lyrics, dialogue and SFX cues with ducking and fx
  (phone, pa, tv, hall, pitch), vocal-stem bleeps, two masters at -14 LUFS.
- `songvid voices`: ElevenLabs designed voices, v3 lines with word timing (auto bleep spans), SFX; cached.
- `songvid image` and `generate.image_provider: gemini`: Nano Banana with reference images.
- Per-shot `video_model` (veo-fast, kling, kling-pro, seedance) and `spend_cap_usd` with a spend.json ledger.
- Storyboard `overlays` (chyron, ticker, bug, caption, card, vs), per-shot `letterbox`, `censor`, and
  `render --uncensored`.

## Quality bar (check before calling it done)

- Would a stranger laugh at least five times on first watch, without knowing the bit?
- Does every chyron earn its frames, or is it noise over a joke that already landed?
- Is the horse the same horse in every shot?
- Can you understand every sung word on a phone speaker?
- Does the mirror beat (bridge) land in one image, without a speech?
- Contact sheet reviewed, then a 30 s slice, then the full render.

## Handoff checklist (for the build session)

1. Keys in the environment: `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, `FAL_KEY`. Never print values.
2. Suno keeper from Joel (Google Drive file, or committed to this branch) -> `songvid suno horse-hospital --import`.
3. Spend cap on paid video generation: **$60 on fal, approved by Joel on 2026-10-06.** That approval
   covers `songvid animate --yes` and direct fal calls up to the cap. Track every paid call in `spend.md`
   (model, seconds, estimated cost, running total) and stop before crossing $60. Stills and ElevenLabs
   are outside the cap but keep them sane.

## Change log

- 2026-10-06: concept, song, and this bible written. Waiting on the Suno keeper and keys.
- 2026-10-06: Built the tooling above while waiting on the song and keys. Default video model veo-fast.
- 2026-10-06: Joel approved: video provider fal, spend cap $60, no vetoes on the cuts or the bleeps.
  Suno settings sent: v6, Male, Duration Auto, Weirdness 30, Style Influence 70, Variety Off,
  Personalize Off, Max Mode off to test the prompt then on for the keeper.
