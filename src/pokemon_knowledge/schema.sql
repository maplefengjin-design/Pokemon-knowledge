PRAGMA foreign_keys = ON;
PRAGMA user_version = 10;

CREATE TABLE source_snapshots (
    source_id TEXT PRIMARY KEY,
    repository_url TEXT NOT NULL,
    upstream_commit TEXT NOT NULL,
    commit_time TEXT,
    fetched_at TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL
);

CREATE TABLE source_licenses (
    source_id TEXT PRIMARY KEY REFERENCES source_snapshots(source_id),
    license_identifier TEXT NOT NULL,
    attribution TEXT NOT NULL,
    license_url TEXT NOT NULL,
    commercial_use_allowed INTEGER NOT NULL
);

CREATE TABLE languages (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    iso639 TEXT,
    iso3166 TEXT,
    official INTEGER NOT NULL
);

CREATE TABLE generations (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE version_groups (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    sort_order INTEGER
);

CREATE TABLE game_versions (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id)
);

CREATE TABLE regions (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE region_names (
    region_id INTEGER NOT NULL REFERENCES regions(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (region_id, language_id)
);

CREATE TABLE pokedexes (
    id INTEGER PRIMARY KEY,
    region_id INTEGER REFERENCES regions(id),
    identifier TEXT NOT NULL UNIQUE,
    is_main_series INTEGER NOT NULL
);

CREATE TABLE pokedex_names (
    pokedex_id INTEGER NOT NULL REFERENCES pokedexes(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY (pokedex_id, language_id)
);

CREATE TABLE pokemon_colors (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE pokemon_color_names (
    color_id INTEGER NOT NULL REFERENCES pokemon_colors(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (color_id, language_id)
);

CREATE TABLE pokemon_shapes (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE pokemon_shape_names (
    shape_id INTEGER NOT NULL REFERENCES pokemon_shapes(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    awesome_name TEXT,
    description TEXT,
    PRIMARY KEY (shape_id, language_id)
);

CREATE TABLE pokemon_habitats (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE pokemon_habitat_names (
    habitat_id INTEGER NOT NULL REFERENCES pokemon_habitats(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (habitat_id, language_id)
);

CREATE TABLE growth_rates (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    formula TEXT NOT NULL
);

CREATE TABLE growth_rate_names (
    growth_rate_id INTEGER NOT NULL REFERENCES growth_rates(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (growth_rate_id, language_id)
);

CREATE TABLE species (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    evolves_from_species_id INTEGER REFERENCES species(id),
    evolution_chain_id INTEGER,
    color_id INTEGER REFERENCES pokemon_colors(id),
    shape_id INTEGER REFERENCES pokemon_shapes(id),
    habitat_id INTEGER REFERENCES pokemon_habitats(id),
    gender_rate INTEGER,
    capture_rate INTEGER,
    base_happiness INTEGER,
    is_baby INTEGER NOT NULL,
    hatch_counter INTEGER,
    growth_rate_id INTEGER REFERENCES growth_rates(id),
    has_gender_differences INTEGER NOT NULL,
    forms_switchable INTEGER NOT NULL,
    is_legendary INTEGER NOT NULL,
    is_mythical INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE species_names (
    species_id INTEGER NOT NULL REFERENCES species(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    genus TEXT,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (species_id, language_id)
);

CREATE TABLE species_flavor_text (
    species_id INTEGER NOT NULL REFERENCES species(id),
    version_id INTEGER NOT NULL REFERENCES game_versions(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    flavor_text TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (species_id, version_id, language_id)
);
CREATE INDEX idx_species_flavor_lookup
    ON species_flavor_text(species_id, language_id, version_id);

CREATE TABLE species_dex_numbers (
    species_id INTEGER NOT NULL REFERENCES species(id),
    pokedex_id INTEGER NOT NULL REFERENCES pokedexes(id),
    pokedex_number INTEGER NOT NULL,
    PRIMARY KEY (species_id, pokedex_id)
);
CREATE INDEX idx_species_dex_lookup ON species_dex_numbers(pokedex_id, pokedex_number);

CREATE TABLE entities (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    identifier TEXT NOT NULL,
    detail_ready INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (entity_type, entity_id)
);

CREATE TABLE entity_aliases (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    alias_normalized TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_kind TEXT NOT NULL,
    language_id INTEGER REFERENCES languages(id),
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (entity_type, alias_normalized, entity_id, alias_kind),
    FOREIGN KEY (entity_type, entity_id) REFERENCES entities(entity_type, entity_id)
);
CREATE INDEX idx_entity_alias_lookup ON entity_aliases(entity_type, alias_normalized);
CREATE INDEX idx_entity_alias_text ON entity_aliases(alias_normalized);

CREATE TABLE entity_descriptions (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    language_id INTEGER NOT NULL REFERENCES languages(id),
    description TEXT NOT NULL,
    description_kind TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (entity_type, entity_id, language_id, source_id),
    FOREIGN KEY (entity_type, entity_id) REFERENCES entities(entity_type, entity_id)
);

CREATE TABLE mechanic_summaries (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    version_group_id INTEGER REFERENCES version_groups(id),
    summary_zh TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    verification_note TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, generation_id),
    FOREIGN KEY (entity_type, entity_id) REFERENCES entities(entity_type, entity_id)
);

-- Long-form, section-level evidence used by the retrieval layer.  Documents
-- keep provenance once; passages remain independently searchable and may be
-- linked to several first-class entities without becoming children of them.
CREATE TABLE knowledge_documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    language_id INTEGER NOT NULL REFERENCES languages(id),
    edition_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    license_identifier TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    content_sha256 TEXT NOT NULL
);

CREATE TABLE knowledge_passages (
    passage_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id),
    section_path TEXT NOT NULL,
    heading TEXT NOT NULL,
    content TEXT NOT NULL,
    generation_from INTEGER REFERENCES generations(id),
    generation_to INTEGER REFERENCES generations(id),
    version_group_id INTEGER REFERENCES version_groups(id),
    edition_id TEXT NOT NULL,
    passage_order INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL
);
CREATE INDEX idx_knowledge_passage_scope
    ON knowledge_passages(edition_id, generation_from, generation_to, version_group_id);

CREATE TABLE passage_entities (
    passage_id TEXT NOT NULL REFERENCES knowledge_passages(passage_id),
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    relation_kind TEXT NOT NULL,
    PRIMARY KEY (passage_id, entity_type, entity_id, relation_kind),
    FOREIGN KEY (entity_type, entity_id) REFERENCES entities(entity_type, entity_id)
);
CREATE INDEX idx_passage_entities_lookup
    ON passage_entities(entity_type, entity_id, passage_id);

-- The trigram tokenizer works well for simplified-Chinese substring retrieval
-- and is available in the Python-bundled SQLite used by this project.
CREATE VIRTUAL TABLE knowledge_passages_fts USING fts5(
    passage_id UNINDEXED,
    heading,
    section_path,
    content,
    tokenize='trigram'
);

-- Faceted discovery tags.  Tags are first-class and reusable; assignments are
-- many-to-many so they can be combined without duplicating entity records.
CREATE TABLE tags (
    tag_id INTEGER PRIMARY KEY,
    tag_key TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL
);
CREATE INDEX idx_tags_category ON tags(category, tag_key);

CREATE TABLE entity_tags (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(tag_id),
    assignment_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (entity_type, entity_id, tag_id),
    FOREIGN KEY (entity_type, entity_id) REFERENCES entities(entity_type, entity_id)
);
CREATE INDEX idx_entity_tags_entity ON entity_tags(entity_type, entity_id, tag_id);
CREATE INDEX idx_entity_tags_tag ON entity_tags(tag_id, entity_type, entity_id);

CREATE VIRTUAL TABLE tags_fts USING fts5(
    tag_id UNINDEXED,
    tag_key UNINDEXED,
    label_zh,
    description_zh,
    tokenize='trigram'
);

CREATE TABLE pokemon_variants (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    species_id INTEGER NOT NULL REFERENCES species(id),
    height_dm INTEGER,
    weight_hg INTEGER,
    base_experience INTEGER,
    is_default INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE pokemon_forms (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL,
    form_identifier TEXT,
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    introduced_in_version_group_id INTEGER REFERENCES version_groups(id),
    is_default INTEGER NOT NULL,
    is_battle_only INTEGER NOT NULL,
    is_mega INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE pokemon_form_names (
    form_id INTEGER NOT NULL REFERENCES pokemon_forms(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    form_name TEXT,
    pokemon_name TEXT,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (form_id, language_id)
);

CREATE TABLE types (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    generation_id INTEGER REFERENCES generations(id)
);

CREATE TABLE type_names (
    type_id INTEGER NOT NULL REFERENCES types(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (type_id, language_id)
);

CREATE TABLE pokemon_types (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    type_id INTEGER NOT NULL REFERENCES types(id),
    slot INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, slot)
);

CREATE TABLE pokemon_types_past (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    type_id INTEGER NOT NULL REFERENCES types(id),
    slot INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, generation_id, slot)
);

CREATE TABLE stats (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    is_battle_only INTEGER NOT NULL
);

CREATE TABLE stat_names (
    stat_id INTEGER NOT NULL REFERENCES stats(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (stat_id, language_id)
);

CREATE TABLE pokemon_stats (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    stat_id INTEGER NOT NULL REFERENCES stats(id),
    base_stat INTEGER NOT NULL,
    effort INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, stat_id)
);

CREATE TABLE pokemon_stats_past (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    stat_id INTEGER NOT NULL REFERENCES stats(id),
    base_stat INTEGER NOT NULL,
    effort INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, generation_id, stat_id)
);

CREATE TABLE abilities (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    generation_id INTEGER REFERENCES generations(id),
    is_main_series INTEGER NOT NULL
);

CREATE TABLE ability_prose (
    ability_id INTEGER NOT NULL REFERENCES abilities(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    short_effect TEXT,
    effect TEXT,
    PRIMARY KEY (ability_id, language_id)
);

CREATE TABLE ability_flavor_text (
    ability_id INTEGER NOT NULL REFERENCES abilities(id),
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    flavor_text TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (ability_id, version_group_id, language_id)
);

CREATE TABLE ability_names (
    ability_id INTEGER NOT NULL REFERENCES abilities(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (ability_id, language_id)
);

CREATE TABLE pokemon_abilities (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    ability_id INTEGER NOT NULL REFERENCES abilities(id),
    is_hidden INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, slot)
);

CREATE TABLE pokemon_abilities_past (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    generation_id INTEGER NOT NULL REFERENCES generations(id),
    ability_id INTEGER REFERENCES abilities(id),
    is_hidden INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE egg_groups (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE egg_group_names (
    egg_group_id INTEGER NOT NULL REFERENCES egg_groups(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (egg_group_id, language_id)
);

CREATE TABLE species_egg_groups (
    species_id INTEGER NOT NULL REFERENCES species(id),
    egg_group_id INTEGER NOT NULL REFERENCES egg_groups(id),
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (species_id, egg_group_id)
);

CREATE TABLE moves (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    generation_id INTEGER REFERENCES generations(id),
    type_id INTEGER REFERENCES types(id),
    power INTEGER,
    pp INTEGER,
    accuracy INTEGER,
    priority INTEGER NOT NULL,
    damage_class_id INTEGER,
    effect_id INTEGER,
    effect_chance INTEGER
);

CREATE TABLE move_names (
    move_id INTEGER NOT NULL REFERENCES moves(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (move_id, language_id)
);

CREATE TABLE move_effect_prose (
    move_effect_id INTEGER NOT NULL,
    language_id INTEGER NOT NULL REFERENCES languages(id),
    short_effect TEXT,
    effect TEXT,
    PRIMARY KEY (move_effect_id, language_id)
);

CREATE TABLE move_flavor_text (
    move_id INTEGER NOT NULL REFERENCES moves(id),
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    flavor_text TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (move_id, version_group_id, language_id)
);

-- Current main-series move mechanism categories. These are kept separate from
-- prose and generic discovery tags so callers can reason over exact membership.
CREATE TABLE move_mechanic_categories (
    identifier TEXT PRIMARY KEY,
    source_flag_identifier TEXT NOT NULL UNIQUE,
    label_zh TEXT NOT NULL,
    description_zh TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE move_mechanic_memberships (
    move_id INTEGER NOT NULL REFERENCES moves(id),
    category_identifier TEXT NOT NULL REFERENCES move_mechanic_categories(identifier),
    ruleset_scope TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (move_id, category_identifier, ruleset_scope)
);
CREATE INDEX idx_move_mechanic_memberships_category
    ON move_mechanic_memberships(category_identifier, move_id);

CREATE TABLE move_mechanic_coverage (
    move_id INTEGER PRIMARY KEY REFERENCES moves(id),
    ruleset_scope TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE item_pockets (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE item_pocket_names (
    item_pocket_id INTEGER NOT NULL REFERENCES item_pockets(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (item_pocket_id, language_id)
);

CREATE TABLE item_categories (
    id INTEGER PRIMARY KEY,
    pocket_id INTEGER NOT NULL REFERENCES item_pockets(id),
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE item_category_names (
    item_category_id INTEGER NOT NULL REFERENCES item_categories(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (item_category_id, language_id)
);

CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES item_categories(id),
    cost INTEGER NOT NULL,
    fling_power INTEGER,
    fling_effect_id INTEGER
);

CREATE TABLE item_names (
    item_id INTEGER NOT NULL REFERENCES items(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (item_id, language_id)
);

CREATE TABLE item_prose (
    item_id INTEGER NOT NULL REFERENCES items(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    short_effect TEXT,
    effect TEXT,
    PRIMARY KEY (item_id, language_id)
);

CREATE TABLE item_flavor_text (
    item_id INTEGER NOT NULL REFERENCES items(id),
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    flavor_text TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (item_id, version_group_id, language_id)
);

CREATE TABLE pokemon_items (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    version_id INTEGER NOT NULL REFERENCES game_versions(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    rarity INTEGER NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id),
    PRIMARY KEY (pokemon_id, version_id, item_id)
);
CREATE INDEX idx_pokemon_items_lookup ON pokemon_items(pokemon_id, version_id);

CREATE TABLE machines (
    machine_number INTEGER NOT NULL,
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    move_id INTEGER NOT NULL REFERENCES moves(id),
    PRIMARY KEY (machine_number, version_group_id)
);

CREATE TABLE move_methods (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE move_method_names (
    method_id INTEGER NOT NULL REFERENCES move_methods(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY (method_id, language_id)
);

CREATE TABLE learnsets (
    pokemon_id INTEGER NOT NULL REFERENCES pokemon_variants(id),
    version_group_id INTEGER NOT NULL REFERENCES version_groups(id),
    move_id INTEGER NOT NULL REFERENCES moves(id),
    method_id INTEGER NOT NULL REFERENCES move_methods(id),
    level INTEGER NOT NULL,
    sort_order INTEGER,
    mastery INTEGER,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);
CREATE INDEX idx_learnsets_lookup ON learnsets(pokemon_id, version_group_id, method_id, move_id);

CREATE TABLE evolution_triggers (
    id INTEGER PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE
);

CREATE TABLE evolution_trigger_names (
    trigger_id INTEGER NOT NULL REFERENCES evolution_triggers(id),
    language_id INTEGER NOT NULL REFERENCES languages(id),
    name TEXT NOT NULL,
    PRIMARY KEY (trigger_id, language_id)
);

CREATE TABLE evolutions (
    id INTEGER PRIMARY KEY,
    from_species_id INTEGER REFERENCES species(id),
    evolved_species_id INTEGER NOT NULL REFERENCES species(id),
    trigger_id INTEGER NOT NULL REFERENCES evolution_triggers(id),
    version_group_id INTEGER REFERENCES version_groups(id),
    is_default INTEGER NOT NULL,
    conditions_json TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES source_snapshots(source_id)
);

CREATE TABLE pilot_species (
    species_id INTEGER PRIMARY KEY REFERENCES species(id)
);

CREATE VIEW default_species_variants AS
SELECT s.id AS species_id, s.identifier AS species_identifier,
       p.id AS pokemon_id, p.identifier AS pokemon_identifier
FROM species s
JOIN pokemon_variants p ON p.species_id = s.id AND p.is_default = 1;
