"""
Defines helper methods useful for loading and caching Interface examples.
"""
from __future__ import annotations

import csv
import inspect
import os
import shutil
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple

import anyio

from gradio import utils
from gradio.components import Dataset
from gradio.context import Context
from gradio.documentation import document, set_documentation_group
from gradio.flagging import CSVLogger

if TYPE_CHECKING:  # Only import for type checking (to avoid circular imports).
    from gradio import Interface
    from gradio.components import Component

CACHED_FOLDER = "gradio_cached_examples"
LOG_FILE = "log.csv"

set_documentation_group("component-helpers")


def create_examples(
    examples: List[Any] | List[List[Any]] | str,
    inputs: Component | List[Component],
    outputs: Optional[Component | List[Component]] = None,
    fn: Optional[Callable] = None,
    cache_examples: bool = False,
    examples_per_page: int = 10,
):
    """Top-level synchronous function that creates Examples. Provided for backwards compatibility, i.e. so that gr.Examples(...) can be used to create the Examples component."""
    examples_obj = Examples(
        examples=examples,
        inputs=inputs,
        outputs=outputs,
        fn=fn,
        cache_examples=cache_examples,
        examples_per_page=examples_per_page,
        _initiated_directly=False,
    )
    utils.synchronize_async(examples_obj.create)
    return examples_obj


@document()
class Examples:
    """
    This class is a wrapper over the Dataset component and can be used to create Examples
    for Blocks / Interfaces. Populates the Dataset component with examples and
    assigns event listener so that clicking on an example populates the input/output
    components. Optionally handles example caching for fast inference.

    Demos: blocks_inputs, fake_gan
    Guides: more_on_examples_and_flagging, using_hugging_face_integrations, image_classification_in_pytorch, image_classification_in_tensorflow, image_classification_with_vision_transformers, create_your_own_friends_with_a_gan
    """

    def __init__(
        self,
        examples: List[Any] | List[List[Any]] | str,
        inputs: Component | List[Component],
        outputs: Optional[Component | List[Component]] = None,
        fn: Optional[Callable] = None,
        cache_examples: bool = False,
        examples_per_page: int = 10,
        _initiated_directly=True,
    ):
        """
        Parameters:
            examples: example inputs that can be clicked to populate specific components. Should be nested list, in which the outer list consists of samples and each inner list consists of an input corresponding to each input component. A string path to a directory of examples can also be provided. If there are multiple input components and a directory is provided, a log.csv file must be present in the directory to link corresponding inputs.
            inputs: the component or list of components corresponding to the examples
            outputs: optionally, provide the component or list of components corresponding to the output of the examples. Required if `cache` is True.
            fn: optionally, provide the function to run to generate the outputs corresponding to the examples. Required if `cache` is True.
            cache_examples: if True, caches examples for fast runtime. If True, then `fn` and `outputs` need to be provided
            examples_per_page: how many examples to show per page (this parameter currently has no effect)
        """
        if _initiated_directly:
            raise warnings.warn(
                "Please use gr.Examples(...) instead of gr.examples.Examples(...) to create the Examples.",
            )

        if cache_examples and (fn is None or outputs is None):
            raise ValueError("If caching examples, `fn` and `outputs` must be provided")

        if not isinstance(inputs, list):
            inputs = [inputs]
        if not isinstance(outputs, list):
            outputs = [outputs]

        working_directory = Path().absolute()

        if examples is None:
            raise ValueError("The parameter `examples` cannot be None")
        elif isinstance(examples, list) and (
            len(examples) == 0 or isinstance(examples[0], list)
        ):
            pass
        elif (
            isinstance(examples, list) and len(inputs) == 1
        ):  # If there is only one input component, examples can be provided as a regular list instead of a list of lists
            examples = [[e] for e in examples]
        elif isinstance(examples, str):
            if not os.path.exists(examples):
                raise FileNotFoundError(
                    "Could not find examples directory: " + examples
                )
            working_directory = examples
            if not os.path.exists(os.path.join(examples, LOG_FILE)):
                if len(inputs) == 1:
                    examples = [[e] for e in os.listdir(examples)]
                else:
                    raise FileNotFoundError(
                        "Could not find log file (required for multiple inputs): "
                        + LOG_FILE
                    )
            else:
                with open(os.path.join(examples, LOG_FILE)) as logs:
                    examples = list(csv.reader(logs))
                    examples = [
                        examples[i][: len(inputs)] for i in range(1, len(examples))
                    ]  # remove header and unnecessary columns

        else:
            raise ValueError(
                "The parameter `examples` must either be a string directory or a list"
                "(if there is only 1 input component) or (more generally), a nested "
                "list, where each sublist represents a set of inputs."
            )

        input_has_examples = [False] * len(inputs)
        for example in examples:
            for idx, example_for_input in enumerate(example):
                if not (example_for_input is None):
                    try:
                        input_has_examples[idx] = True
                    except IndexError:
                        pass  # If there are more example components than inputs, ignore. This can sometimes be intentional (e.g. loading from a log file where outputs and timestamps are also logged)

        inputs_with_examples = [
            inp for (inp, keep) in zip(inputs, input_has_examples) if keep
        ]
        non_none_examples = [
            [ex for (ex, keep) in zip(example, input_has_examples) if keep]
            for example in examples
        ]

        self.examples = examples
        self.non_none_examples = non_none_examples
        self.inputs = inputs
        self.inputs_with_examples = inputs_with_examples
        self.outputs = outputs
        self.fn = fn
        self.cache_examples = cache_examples
        self.examples_per_page = examples_per_page

        with utils.set_directory(working_directory):
            self.processed_examples = [
                [
                    component.preprocess_example(sample)
                    for component, sample in zip(inputs_with_examples, example)
                ]
                for example in non_none_examples
            ]

        self.dataset = Dataset(
            components=inputs_with_examples,
            samples=non_none_examples,
            type="index",
        )

        self.cached_folder = os.path.join(CACHED_FOLDER, str(self.dataset._id))
        self.cached_file = os.path.join(self.cached_folder, "log.csv")
        self.cache_examples = cache_examples

    async def create(self) -> None:
        """Caches the examples if self.cache_examples is True and creates the Dataset
        component to hold the examples"""
        if self.cache_examples:
            await self.cache_interface_examples()

        async def load_example(example_id):
            if self.cache_examples:
                processed_example = self.processed_examples[
                    example_id
                ] + await self.load_from_cache(example_id)
            else:
                processed_example = self.processed_examples[example_id]
            return utils.resolve_singleton(processed_example)

        if Context.root_block:
            self.dataset.click(
                load_example,
                inputs=[self.dataset],
                outputs=self.inputs_with_examples
                + (self.outputs if self.cache_examples else []),
                _postprocess=False,
                queue=False,
            )

    async def cache_interface_examples(self) -> None:
        """Caches all of the examples from an interface."""
        if os.path.exists(self.cached_file):
            print(
                f"Using cache from '{os.path.abspath(self.cached_folder)}' directory. If method or examples have changed since last caching, delete this folder to clear cache."
            )
        else:
            print(f"Caching examples at: '{os.path.abspath(self.cached_file)}'")
            cache_logger = CSVLogger()
            cache_logger.setup(self.outputs, self.cached_folder)
            for example_id, _ in enumerate(self.examples):
                try:
                    prediction = await self.process_example(example_id)
                    cache_logger.flag(prediction)
                except Exception as e:
                    shutil.rmtree(self.cached_folder)
                    raise e

    async def process_example(self, example_id: int) -> Tuple[List[Any], List[float]]:
        """Loads an example from the interface and returns its prediction.
        Parameters:
            example_id: The id of the example to process (zero-indexed).
        """
        example_set = self.examples[example_id]
        raw_input = [
            self.inputs[i].preprocess_example(example)
            for i, example in enumerate(example_set)
        ]
        processed_input = [
            input_component.preprocess(raw_input[i])
            for i, input_component in enumerate(self.inputs)
        ]
        if inspect.iscoroutinefunction(self.fn):
            predictions = await self.fn(*processed_input)
        else:
            predictions = await anyio.to_thread.run_sync(self.fn, *processed_input)
        if len(self.outputs) == 1:
            predictions = [predictions]
        processed_output = [
            output_component.postprocess(predictions[i])
            if predictions[i] is not None
            else None
            for i, output_component in enumerate(self.outputs)
        ]

        return processed_output

    async def load_from_cache(self, example_id: int) -> List[Any]:
        """Loads a particular cached example for the interface.
        Parameters:
            example_id: The id of the example to process (zero-indexed).
        """
        with open(self.cached_file) as cache:
            examples = list(csv.reader(cache, quotechar="'"))
        example = examples[example_id + 1]  # +1 to adjust for header
        output = []
        for component, cell in zip(self.outputs, example):
            output.append(
                component.restore_flagged(
                    self.cached_folder,
                    cell,
                    None,
                )
            )
        return output
