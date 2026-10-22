// Local-only editor harness: exercise the real structural command adapter.
window.installEditor = models => {
  let sequence = 0;
  const build = (model, parent = null) => {
    const id = model.id || 'generated-' + (++sequence);
    const node = {id, parent, children: [], raw: model};
    node.children = (model.elements || []).map(child => build(child, node));
    node.model = {
      get: name => name === 'elType' ? model.elType : model[name],
      toJSON: () => ({...model, id, elements: node.children.map(child => child.model.toJSON())}),
    };
    return node;
  };
  const root = build({id: 'root', elType: 'document', elements: models});
  const render = () => {
    const mount = document.querySelector('main');
    mount.replaceChildren();
    const draw = node => {
      const element = document.createElement('div');
      element.dataset.id = node.id;
      if (node.raw.elType === 'widget') {
        const type = node.raw.widgetType;
        element.className = 'elementor-widget elementor-widget-' + type;
        element.style.cssText = 'flex:1;min-width:0;min-height:50px';
        const text = document.createElement(type === 'heading' ? 'h2' : 'p');
        text.textContent = node.raw.settings?.title ?? node.raw.settings?.editor ?? '';
        element.append(text);
      } else {
        element.className = 'e-con ' + (node.raw.settings?._css_classes || '');
        element.style.cssText = 'display:flex;min-width:0;flex-direction:' + (node.raw.settings?.flex_direction || 'column');
        if (node.raw.settings?.container_type === 'grid') {
          element.style.display = 'grid';
          element.style.gridTemplateColumns = 'repeat(' + node.raw.settings.grid_columns_grid.size + ',1fr)';
        }
        node.children.forEach(child => element.append(draw(child)));
      }
      return element;
    };
    root.children.forEach(child => mount.append(draw(child)));
  };
  window.elementor = {documents: {getCurrent: () => ({container: root})}};
  window.$e = {run: async (command, args) => {
    const container = args.container;
    if (command.endsWith('/create')) {
      const validate = item => {
        if (!item.id) throw new Error('Nested Elementor models require explicit IDs');
        (item.elements || []).forEach(validate);
      };
      validate(args.model);
      const node = build(args.model, container);
      container.children.splice(args.options.at, 0, node);
      render(); return node;
    }
    if (command.endsWith('/delete')) {
      container.parent.children = container.parent.children.filter(child => child !== container);
    } else if (command.endsWith('/settings')) {
      container.raw.settings = {...container.raw.settings, ...args.settings};
    } else throw new Error('Unsupported mock command: ' + command);
    render();
  }};
  render();
};
