# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 OpenCraft
# License: AGPLv3
"""
This module contains a mixin that allows third party XBlocks to be easily edited within edX
Studio just like the built-in modules. No configuration required, just add
StudioEditableXBlockMixin to your XBlock.
"""

# Imports ###########################################################

import json
import logging

from django.utils.translation import ugettext
from xblock.core import XBlock
from xblock.fields import Scope, JSONField, List, Integer, Float, Boolean, String
from xblock.exceptions import JsonHandlerError
from xblock.fragment import Fragment
from xblock.validation import Validation

from xblockutils.resources import ResourceLoader

# Globals ###########################################################

log = logging.getLogger(__name__)
loader = ResourceLoader(__name__)

# Classes ###########################################################


class FutureFields(object):
    """
    A helper class whose attribute values come from the specified dictionary or fallback object.

    This is only used by StudioEditableXBlockMixin and is not meant to be re-used anywhere else!

    This class wraps an XBlock and makes it appear that some of the block's field values have
    been changed to new values or deleted (and reset to default values). It does so without
    actually modifying the XBlock. The only reason we need this is because the XBlock validation
    API is built around attribute access, but often we want to validate data that's stored in a
    dictionary before making changes to an XBlock's attributes (since any changes made to the
    XBlock may get persisted even if validation fails).
    """
    def __init__(self, new_fields_dict, newly_removed_fields, fallback_obj):
        """
        Create an instance whose attributes come from new_fields_dict and fallback_obj.

        Arguments:
        new_fields_dict -- A dictionary of values that will appear as attributes of this object
        newly_removed_fields -- A list of field names for which we will not use fallback_obj
        fallback_obj -- An XBlock to use as a provider for any attributes not in new_fields_dict
        """
        self._new_fields_dict = new_fields_dict
        self._blacklist = newly_removed_fields
        self._fallback_obj = fallback_obj

    def __getattr__(self, name):
        try:
            return self._new_fields_dict[name]
        except KeyError:
            if name in self._blacklist:
                # Pretend like this field is not actually set, since we're going to be resetting it to default
                return self._fallback_obj.fields[name].default
            return getattr(self._fallback_obj, name)


class StudioEditableXBlockMixin(object):
    """
    An XBlock mixin to provide a configuration UI for an XBlock in Studio.
    """
    editable_fields = ()  # Set this to a list of the names of fields to appear in the editor

    def studio_view(self, context):
        """
        Render a form for editing this XBlock
        """
        fragment = Fragment()
        context = {'fields': []}
        # Build a list of all the fields that can be edited:
        for field_name in self.editable_fields:
            field = self.fields[field_name]
            assert field.scope in (Scope.content, Scope.settings), (
                "Only Scope.content or Scope.settings fields can be used with "
                "StudioEditableXBlockMixin. Other scopes are for user-specific data and are "
                "not generally created/configured by content authors in Studio."
            )
            field_info = self._make_field_info(field_name, field)
            if field_info is not None:
                context["fields"].append(field_info)
        fragment.content = loader.render_template('templates/studio_edit.html', context)
        fragment.add_javascript(loader.load_unicode('public/studio_edit.js'))
        fragment.initialize_js('StudioEditableXBlockMixin')
        return fragment

    def _make_field_info(self, field_name, field):
        """
        Create the information that the template needs to render a form field for this field.
        """
        supported_field_types = (
            (Integer, 'integer'),
            (Float, 'float'),
            (Boolean, 'boolean'),
            (String, 'string'),
            (List, 'list'),
            (JSONField, 'generic'),  # This is last so as a last resort we display a text field w/ the JSON string
        )
        info = {
            'name': field_name,
            'display_name': ugettext(field.display_name) if field.display_name else "",
            'is_set': field.is_set_on(self),
            'default': field.default,
            'value': field.read_from(self),
            'has_values': False,
            'help': ugettext(field.help) if field.help else "",
            'allow_reset': field.runtime_options.get('resettable_editor', True),
            'list_values': None,  # Only available for List fields
            'has_list_values': False,  # True if list_values_provider exists, even if it returned no available options
        }
        for type_class, type_name in supported_field_types:
            if isinstance(field, type_class):
                info['type'] = type_name
                # If String fields are declared like String(..., multiline_editor=True), then call them "text" type:
                editor_type = field.runtime_options.get('multiline_editor')
                if type_class is String and editor_type:
                    if editor_type == "html":
                        info['type'] = 'html'
                    else:
                        info['type'] = 'text'
                if type_class is List and field.runtime_options.get('list_style') == "set":
                    # List represents unordered, unique items, optionally drawn from list_values_provider()
                    info['type'] = 'set'
                elif type_class is List:
                    info['type'] = "generic"  # disable other types of list for now until properly implemented
                break
        if "type" not in info:
            raise NotImplementedError("StudioEditableXBlockMixin currently only supports fields derived from JSONField")
        if info["type"] in ("list", "set"):
            info["value"] = [json.dumps(val) for val in info["value"]]
            info["default"] = json.dumps(info["default"])
        elif info["type"] == "generic":
            # Convert value to JSON string if we're treating this field generically:
            info["value"] = json.dumps(info["value"])
            info["default"] = json.dumps(info["default"])

        if 'values_provider' in field.runtime_options:
            values = field.runtime_options["values_provider"](self)
        else:
            values = field.values
        if values and not isinstance(field, Boolean):
            # This field has only a limited number of pre-defined options.
            # Protip: when defining the field, values= can be a callable.
            if isinstance(field.values, dict) and isinstance(field, (Float, Integer)):
                # e.g. {"min": 0 , "max": 10, "step": .1}
                for option in field.values:
                    if option in ("min", "max", "step"):
                        info[option] = field.values.get(option)
                    else:
                        raise KeyError("Invalid 'values' key. Should be like values={'min': 1, 'max': 10, 'step': 1}")
            elif isinstance(values[0], dict) and "display_name" in values[0] and "value" in values[0]:
                # e.g. [ {"display_name": "Always", "value": "always"}, ... ]
                for value in values:
                    assert "display_name" in value and "value" in value
                info['values'] = values
            else:
                # e.g. [1, 2, 3] - we need to convert it to the [{"display_name": x, "value": x}] format
                info['values'] = [{"display_name": unicode(val), "value": val} for val in values]
            info['has_values'] = 'values' in info
        if info["type"] in ("list", "set") and field.runtime_options.get('list_values_provider'):
            list_values = field.runtime_options['list_values_provider'](self)
            # list_values must be a list of values or {"display_name": x, "value": y} objects
            # Furthermore, we need to convert all values to JSON since they could be of any type
            if list_values and isinstance(list_values[0], dict) and "display_name" in list_values[0]:
                # e.g. [ {"display_name": "Always", "value": "always"}, ... ]
                for entry in list_values:
                    assert "display_name" in entry and "value" in entry
                    entry["value"] = json.dumps(entry["value"])
            else:
                # e.g. [1, 2, 3] - we need to convert it to the [{"display_name": x, "value": x}] format
                list_values = [json.dumps(val) for val in list_values]
                list_values = [{"display_name": unicode(val), "value": val} for val in list_values]
            info['list_values'] = list_values
            info['has_list_values'] = True
        return info

    @XBlock.json_handler
    def submit_studio_edits(self, data, suffix=''):
        """
        AJAX handler for studio_view() Save button
        """
        values = {}  # dict of new field values we are updating
        to_reset = []  # list of field names to delete from this XBlock
        for field_name in self.editable_fields:
            field = self.fields[field_name]
            if field_name in data['values']:
                if isinstance(field, JSONField):
                    values[field_name] = field.from_json(data['values'][field_name])
                else:
                    raise JsonHandlerError(400, "Unsupported field type: {}".format(field_name))
            elif field_name in data['defaults'] and field.is_set_on(self):
                to_reset.append(field_name)
        self.clean_studio_edits(values)
        validation = Validation(self.scope_ids.usage_id)
        # We cannot set the fields on self yet, because even if validation fails, studio is going to save any changes we
        # make. So we create a "fake" object that has all the field values we are about to set.
        preview_data = FutureFields(
            new_fields_dict=values,
            newly_removed_fields=to_reset,
            fallback_obj=self
        )
        self.validate_field_data(validation, preview_data)
        if validation:
            for field_name, value in values.iteritems():
                setattr(self, field_name, value)
            for field_name in to_reset:
                self.fields[field_name].delete_from(self)
            return {'result': 'success'}
        else:
            raise JsonHandlerError(400, validation.to_json())

    def clean_studio_edits(self, data):
        """
        Given POST data dictionary 'data', clean the data before validating it.
        e.g. fix capitalization, remove trailing spaces, etc.
        """
        # Example:
        # if "name" in data:
        #     data["name"] = data["name"].strip()
        pass

    def validate_field_data(self, validation, data):
        """
        Validate this block's field data. Instead of checking fields like self.name, check the
        fields set on data, e.g. data.name. This allows the same validation method to be re-used
        for the studio editor. Any errors found should be added to "validation".

        This method should not return any value or raise any exceptions.
        All of this XBlock's fields should be found in "data", even if they aren't being changed
        or aren't even set (i.e. are defaults).
        """
        # Example:
        # if data.count <=0:
        #     validation.add(ValidationMessage(ValidationMessage.ERROR, u"Invalid count"))
        pass

    def validate(self):
        """
        Validates the state of this XBlock.

        Subclasses should override validate_field_data() to validate fields and override this
        only for validation not related to this block's field values.
        """
        validation = super(StudioEditableXBlockMixin, self).validate()
        self.validate_field_data(validation, self)
        return validation


class StudioContainerXBlockMixin(object):
    """
    An XBlock mixin to provide convenient use of an XBlock in Studio
    that wants to allow the user to assign children to it.
    """
    has_author_view = True  # Without this flag, studio will use student_view on newly-added blocks :/

    def render_children(self, context, fragment, can_reorder=True, can_add=False):
        """
        Renders the children of the module with HTML appropriate for Studio. If can_reorder is
        True, then the children will be rendered to support drag and drop.
        """
        contents = []

        for child_id in self.children:
            child = self.runtime.get_block(child_id)
            if can_reorder:
                context['reorderable_items'].add(child.scope_ids.usage_id)
            rendered_child = child.render('author_view' if hasattr(child, 'author_view') else 'student_view', context)
            fragment.add_frag_resources(rendered_child)

            contents.append({
                'id': child.location.to_deprecated_string(),
                'content': rendered_child.content
            })

        fragment.add_content(self.runtime.render_template("studio_render_children_view.html", {
            'items': contents,
            'xblock_context': context,
            'can_add': can_add,
            'can_reorder': can_reorder,
        }))

    def author_view(self, context):
        """
        Display a the studio editor when the user has clicked "View" to see the container view,
        otherwise just show the normal 'author_preview_view' or 'student_view' preview.
        """
        root_xblock = context.get('root_xblock')

        if root_xblock and root_xblock.location == self.location:
            # User has clicked the "View" link. Show an editable preview of this block's children
            return self.author_edit_view(context)
        else:
            return self.author_preview_view(context)

    def author_edit_view(self, context):
        """
        Child blocks can override this to control the view shown to authors in Studio when
        editing this block's children.
        """
        fragment = Fragment()
        self.render_children(context, fragment, can_reorder=True, can_add=False)
        return fragment

    def author_preview_view(self, context):
        """
        Child blocks can override this to add a custom preview shown to authors in Studio when
        not editing this block's children.
        """
        return self.student_view(context)
