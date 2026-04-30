-- Seed initial workflow pattern
INSERT INTO workflow_patterns (name, version, steps, status, consistency_score, vote_count)
VALUES (
    'locked-in-v1',
    1,
    '[
        {"step": 1, "action": "Create song", "use_style": true, "use_lyrics": true, "note": "Pick best clip"},
        {"step": 2, "action": "Cover", "use_style": true, "use_lyrics": false, "note": "Locks the sound"},
        {"step": 3, "action": "Extend from 0:01", "use_style": false, "use_lyrics": true, "note": "Power refresh — pick best"},
        {"step": 4, "action": "Get Full Song", "use_style": false, "use_lyrics": false, "note": ""},
        {"step": 5, "action": "Cover Full Song", "use_style": true, "use_lyrics": true, "note": "Unlocks Extend"},
        {"step": 6, "action": "Extend from end", "use_style": false, "use_lyrics": false, "note": "Creates ghost track"},
        {"step": 7, "action": "Cover ghost track", "use_style": true, "use_lyrics": false, "note": "Powers up ghost track"},
        {"step": 8, "action": "Extend from where lyrics start", "use_style": false, "use_lyrics": true, "note": "Refines vocal entry"},
        {"step": 9, "action": "Get Full Song", "use_style": false, "use_lyrics": false, "note": ""},
        {"step": 10, "action": "Cover the Full Song", "use_style": false, "use_lyrics": false, "note": ""},
        {"step": 11, "action": "Cover the Cover", "use_style": false, "use_lyrics": false, "note": "Final output"}
    ]'::jsonb,
    'active',
    100.00,
    1
)
ON CONFLICT (name, version) DO NOTHING;
