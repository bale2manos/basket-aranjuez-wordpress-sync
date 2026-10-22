"""Conservative structural operations on native Elementor match widgets.

Text is still edited through the widget panel. These operations only clone or
remove scoped native elements; they never replace a post's HTML/data document.
"""
from __future__ import annotations


def reconcile_match_layout(anchor, matches, period_counts: list[int]) -> bool:
    if not period_counts:
        raise ValueError("No hay partidos que actualizar.")
    payload = []
    metadata_ids = set()
    for match in matches:
        fields = match._structured_field_widgets()
        metadata_ids.update(str(w["dataId"]) for k, w in fields.items() if k in {"team_name", "competition", "group"})
        ids = {k: str(w["dataId"]) for k, w in fields.items()}
        top = min(fields[k]["y"] for k in ("home_team", "score", "away_team")) - 5
        bottom = fields["chronicle_text"]["y"]
        removable = [str(w["dataId"]) for w in match._block_widgets()
                     if w["y"] >= top and (w["y"] <= bottom or w["type"] == "match_end")]
        payload.append({"fields": ids, "remove": removable})
    existing_counts = [sum(k.startswith("period_label_") for k in m["fields"]) for m in payload]
    if len(matches) == len(period_counts) and all(have >= need for have, need in zip(existing_counts, period_counts)):
        return False
    return bool(anchor.preview.page.evaluate(RECONCILE_SCRIPT, {
        "matches": payload, "periodCounts": period_counts, "protectedIds": sorted(metadata_ids),
    }))


RECONCILE_SCRIPT = r"""async ({matches, periodCounts, protectedIds}) => {
  const root = window.elementor?.documents?.getCurrent?.()?.container;
  if (!root || !window.$e) throw new Error('Elementor no esta preparado');
  const find = (node, id) => node.id === id ? node : (node.children || []).map(n => find(n, id)).find(Boolean);
  const widgetIds = node => node.model?.get('elType') === 'widget' ? [node.id] : (node.children || []).flatMap(widgetIds);
  const clean = model => {
    const data = JSON.parse(JSON.stringify(model));
    const visit = item => { delete item.id; (item.elements || []).forEach(visit); };
    visit(data); return data;
  };
  const copy = id => {
    const node = find(root, id);
    if (!node) throw new Error('Falta un widget de plantilla: ' + id);
    return clean(node.model.toJSON());
  };
  const row = elements => ({elType: 'container', settings: {content_width: 'full', flex_direction: 'row', flex_wrap: 'nowrap'}, elements});
  const cloneWidget = id => {
    const model = copy(id);
    model.settings = {...model.settings, _element_width: 'initial', _flex_size: 'grow'};
    return model;
  };
  const create = (parent, model, at) => {
    const data = clean(model);
    // Elementor assigns an ID to the root only; nested models need their own
    // IDs before creation so their views and scoped styles can be rendered.
    const identify = item => {
      item.id = crypto.getRandomValues(new Uint32Array(1))[0].toString(16).padStart(8, '0');
      (item.elements || []).forEach(identify);
    };
    identify(data);
    return window.$e.run('document/elements/create', {container: parent, model: data, options: {at}});
  };
  const deleteTargets = async ids => {
    const targets = new Set(ids);
    if (protectedIds.some(id => targets.has(id))) throw new Error('El borrado incluiria la cabecera del equipo');
    const candidates = [];
    const collect = node => {
      const ids = widgetIds(node);
      if (node !== root && ids.length && ids.every(id => targets.has(id))) candidates.push(node);
      else (node.children || []).forEach(collect);
    };
    collect(root);
    const covered = new Set(candidates.flatMap(widgetIds));
    if (ids.some(id => !covered.has(id))) throw new Error('No se puede aislar el partido sobrante');
    for (const node of candidates.reverse()) await window.$e.run('document/elements/delete', {container: node});
  };
  // Resolve every source before mutating anything.
  const source = matches[0].fields;
  const periodKeys = Object.keys(source).filter(k => k.startsWith('period_label_')).sort((a,b) => +a.split('_').pop() - +b.split('_').pop());
  if (!periodKeys.length) throw new Error('La plantilla no tiene columnas de parciales');
  const models = {
    scoreboard: ['home_team','score','away_team'].map(k => cloneWidget(source[k])),
    label: cloneWidget(source[periodKeys[0]]),
    home: cloneWidget(source.home_period_1), away: cloneWidget(source.away_period_1),
    homeName: cloneWidget(source.table_home_name), awayName: cloneWidget(source.table_away_name),
    roster: copy(source.players_text), body: copy(source.chronicle_text),
  };
  // Extra native matches are removed as complete, isolated widget subtrees.
  for (let i = matches.length - 1; i >= periodCounts.length; --i) await deleteTargets(matches[i].remove);
  for (let i = 0; i < Math.min(matches.length, periodCounts.length); ++i) {
    const fields = matches[i].fields;
    const have = Object.keys(fields).filter(k => k.startsWith('period_label_')).length;
    if (have >= periodCounts[i]) continue;
    for (const prefix of ['period_label', 'home_period', 'away_period']) {
      const ids = Array.from({length: have}, (_, j) => fields[prefix + '_' + (j + 1)]);
      const nodes = ids.map(id => find(root, id));
      if (nodes.some(n => !n)) throw new Error('No se puede aislar la fila de parciales');
      let parent = nodes[0].parent;
      while (parent && !nodes.every(n => widgetIds(parent).includes(n.id))) parent = parent.parent;
      if (!parent || parent === root) throw new Error('La tabla comparte estructura con otros equipos');
      const cells = nodes.map(n => { while (n.parent !== parent) n = n.parent; return n; });
      if (new Set(cells.map(n => n.id)).size !== have) throw new Error('Las celdas no se pueden duplicar de forma independiente');
      const template = clean(cells[have - 1].model.toJSON());
      const current = parent.model.get('settings');
      const getSetting = key => current?.get ? current.get(key) : current?.[key];
      const columns = parent.children.length + periodCounts[i] - have;
      const layout = getSetting('container_type') === 'grid' ? {
        grid_columns_grid: {unit: 'fr', size: columns},
        grid_columns_grid_tablet: {unit: 'fr', size: columns},
        grid_columns_grid_mobile: {unit: 'fr', size: columns},
      } : {flex_wrap: 'nowrap'};
      await window.$e.run('document/elements/settings', {container: parent, settings: layout});
      for (let j = have; j < periodCounts[i]; ++j) await create(parent, clean(template), parent.children.length);
    }
  }
  // Additional games inherit appearance and roster, never the team heading or sponsor.
  const lastFields = matches[Math.min(matches.length, periodCounts.length) - 1].fields;
  const lastBody = find(root, lastFields.chronicle_text);
  if (!lastBody?.parent) throw new Error('No hay un punto de insercion seguro para el partido');
  // Legacy columns cannot contain flex containers. Insert after the enclosing
  // top-level section instead; this also avoids narrowing the new scoreboard.
  let insertionNode = lastBody;
  while (insertionNode.parent && insertionNode.parent !== root) insertionNode = insertionNode.parent;
  if (insertionNode.parent !== root) throw new Error('La cronica no pertenece al documento actual');
  let insertion = root.children.findIndex(n => n.id === insertionNode.id) + 1;
  for (let i = matches.length; i < periodCounts.length; ++i) {
    const count = periodCounts[i];
    const model = {elType: 'container', settings: {content_width: 'full', flex_direction: 'column', _css_classes: 'basket-sync-match'}, elements: [
      row(models.scoreboard),
      row([{elType: 'widget', widgetType: 'spacer', settings: {space: {unit: 'px', size: 1}, _flex_size: 'grow'}, elements: []}, ...Array.from({length: count}, (_, j) => ({...models.label, settings: {...models.label.settings, title: (j + 1) + 'C'}}))]),
      row([models.homeName, ...Array.from({length: count}, () => models.home)]),
      row([models.awayName, ...Array.from({length: count}, () => models.away)]),
      models.roster, models.body,
    ]};
    await create(root, clean(model), insertion++);
  }
  return true;
}"""
