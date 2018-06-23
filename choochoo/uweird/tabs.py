
from collections.abc import Sequence

from urwid import WidgetWrap, emit_signal, connect_signal, Widget, ExitMainLoop

from .focus import Focus, FocusAttr


# new tab manager design

# tabs can be arranged in groups.  group contents can be wiped and re-added.
# this allows tabs in the "middle" of a travers to be rebuilt.
# tabs are assembled in a TabList and then passed to a TabNode which
# contains the group.  the TabNode has to be a widget itself since it
# needs to re-raise signals for tabbing.  because it is a WidgetWrap we need
# the intermediate TabList to assemble the group contents (since the
# TabNode will often be created later).  groups can be nested (a TabNode
# can appear in a TabList) and will behave correctly.  the top-most TabNode
# must have discover() called to set signals for tab looping and to discover
# focuses.

# the functionality depends on Focus.apply taking a keypress argument which
# is duplicated by TabNodes.  on TabNodes this triggers internal logic.


class Tab(WidgetWrap):
    """
    A widget wrapper that is added automatically by TagList.add().  Must
    be added to any node that is both target and source of tabbing.
    Intercepts tab keypresses and raises a signal that causes the focus to
    change.

    Normal use is:
        tabs = TabList()
        ...
        widget = tabs.add(Widget(...))
    """

    signals = ['tab']

    def keypress(self, size, key):
        # todo - pass to super first and only handle tabs that are not handled
        # by the widget?
        if key in ('tab', 'shift tab'):
            emit_signal(self, 'tab', self, key)
        else:
            return super().keypress(size, key)


class TabList(Sequence):
    """
    A list of tabbed widgets (in tabbing order) that will be managed by a TabNode.
    The list allows these to be assembled before the TabNode instance is created.
    May include both widgets and other TabNode instances.
    """

    def __init__(self):
        """
        Create an empty list.
        """
        self.__tabs = []

    def add(self, widget_or_node):
        """
        Add a widget to the list of managed widgets.  The return valuie should be
        used in the constructed tree of widgets (it contains both a Tab target and
        a FocusAttr).
        """
        # todo - how do we modify FocusAttr?
        is_node = isinstance(widget_or_node, TabNode)
        widget_or_node = widget_or_node if is_node else Tab(FocusAttr(widget_or_node))
        self.__tabs.append(widget_or_node)
        return widget_or_node

    def __getitem__(self, item):
        return self.__tabs[item]

    def __len__(self):
        return len(self.__tabs)


class TabNode(WidgetWrap):
    """
    A widget wrapper that encapsulates a (local) root node in the widget tree and
    manages all the tabs below that node.

    In dynamic applications the entire TabList may be replaced using replace_all().
    If only a subset of all nodes need to be replaced, use a nested TabNode (so
    the entire contents of the nested node are replaced).

    Normal use is:
        tabs = TabList()
        widget1 = tabs.add(Widget(...))
        ...
        widgetN = tabs.add(Widget(...))
        root = TabNode(Container([widget1, ... windgetN]), tabs)
        root.discover()
    """

    signals = ['tab']

    def __init__(self, widget, tab_list):
        """
        Create a (local) root to the widget tree that manages tabs to the widgets
        below (possibly via nested TabNode instances).
        """
        super().__init__(widget)
        self.__tabs_and_indices = {}
        self.__focus = {}
        self.__root = None
        self.__top = False
        self.__build_data(tab_list)

    def __build_data(self, tab_list):
        for tab in tab_list:
            n = len(self.__focus)
            self.__tabs_and_indices[tab] = n
            self.__tabs_and_indices[n] = tab
            self.__focus[tab] = None
            connect_signal(tab, 'tab', self.tab)

    def replace_all(self, tab_list):
        """
        Replace all the managed tabs.  Typically used at the local root of a dynamic
        section of the widget tree.
        """
        self.__tabs_and_indices = {}
        self.__focus = {}
        self.__build_data(tab_list)

    def tab(self, tab, key):
        """
        The target for tab signals from managed Tab() instances.

        On receiving a signal:
        * check whether tabbing can be handled locally and, if so, activate
        * check if we are root and, if so, loop around
        * otherwise re-raise to tab to remote neighbours (from nested node)
        """
        delta = 1 if key == 'tab' else -1
        n = self.__tabs_and_indices[tab] + delta
        if 0 <= n < len(self.__focus):
            self.__try_set_focus(n, key)
        elif self.__top:
            self.to(None, key)
        else:
            emit_signal(self, 'tab', self, key)

    def __try_set_focus(self, n, key):
        try:
            self.__set_focus(self.__tabs_and_indices[n], key)
        except AttributeError:
            self.discover(self.__root)
            self.__set_focus(self.__tabs_and_indices[n], key)

    def __set_focus(self, tab, key):
        self.__focus[tab].to(self.__root, key)

    def to(self, root, key):
        """
        Replicate the Focus() interface.  This is used internally for sub-nodes.
        Instead of assigning focus using Focus.to(),
        """
        if self.__focus:
            n = 0 if key == 'tab' else len(self.__focus) - 1
            self.__try_set_focus(n, key)
        else:
            # we have nothing to focus, so re-raise signal for remote neighbours
            emit_signal(self, 'tab', self, key)

    def discover(self, root=None, top=True):
        """
        Register the root widget here before use (in many cases the root node is
        also this TabNode, so no root argument is needed).

        Does a search of the entire widget tree, recording paths to added widgets
        so that they can be given focus quickly.
        """
        if top:
            self.__top = True
        if root is None:
            root = self
        self.__root = root
        stack = [(root, [])]
        while stack:
            node, path = stack.pop()
            try:
                # contents can be list or dict
                try:
                    iterator = node.contents.items()
                except AttributeError:
                    try:
                        # possibly a dict
                        iterator = enumerate(node.contents)
                    except TypeError:
                        # possibly a ListBox
                        iterator = node.contents.body.items()
                for (key, data) in iterator:
                    # data can be widget or tuple containing widget
                    try:
                        iter(data)
                    except TypeError:
                        data = [data]
                    new_path = list(path) + [key]
                    for widget in data:
                        if isinstance(widget, Widget):
                            if widget in self.__focus:
                                if isinstance(widget, TabNode):
                                    self.__focus[widget] = widget
                                    widget.discover(root, top=False)
                                else:
                                    self.__focus[widget] = Focus(new_path)
                            else:
                                stack.append((widget, new_path))
            except AttributeError:
                if hasattr(node, '_original_widget'):
                    widget = node._original_widget
                elif hasattr(node, '_wrapped_widget'):
                    widget = node._wrapped_widget
                else:
                    widget = None
                if widget:
                    if widget in self.__focus:
                        if isinstance(widget, TabNode):
                            self.__focus[widget] = widget
                            widget.discover(root, top=False)
                        else:
                            self.__focus[widget] = Focus(path)
                    else:
                        stack.append((widget, path))
        for widget in self.__focus:
            if not self.__focus[widget]:
                raise Exception('Could not find %s' % widget)


class Root(TabNode):

    def __init__(self, widget, tab_list, quit='meta q', save='meta s', saves=None):
        super().__init__(widget, tab_list)
        self.__quit = quit
        self.__save = save
        self.__save_callbacks = []
        if saves: self.add_saves(saves)

    def add_saves(self, callbacks):
        self.__save_callbacks.extend(callbacks)

    def keypress(self, size, key):
        if key == self.__quit:
            self.save()
            raise ExitMainLoop()
        elif key == self.__save:
            self.save()
        else:
            return super().keypress(size, key)

    def save(self):
        for callback in self.__save_callbacks:
            callback(None)
