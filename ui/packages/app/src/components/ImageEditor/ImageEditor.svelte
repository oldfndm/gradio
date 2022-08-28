<script lang="ts">
	import { createEventDispatcher, onMount, onDestroy } from "svelte";
	import { Image, StaticImage } from "@gradio/image";
	import { Block } from "@gradio/atoms";
	import { _ } from "svelte-i18n";
	import { Component as StatusTracker } from "../StatusTracker/";
	import type { LoadingStatus } from "../StatusTracker/types";
	import type { Styles } from "@gradio/utils";

	export let elem_id: string = "";
	export let visible: boolean = true;
	export let value: { image: string | null; mask: string | null };
	export let style: Styles = {};

	export let loading_status: LoadingStatus;

	let dragging: boolean;
  let imageEditor: HTMLIFrameElement;

  let imageEditorValue: string | null = null;
  let mask: string | null = null;

  const dispatch = createEventDispatcher<{
		change: { image: string | null; mask: string | null };
		edit: undefined;
	}>();

  const compareValues = (val: any) => {
    if (imageEditor && imageEditorValue !== val.image) {
      imageEditor.contentWindow?.postMessage({
        type: "image-editor-input",
        image: value.image
      }, '*');
    }
  }

  $: {
    compareValues(value);
  }

  function processMessages(e: MessageEvent) {
    if (e.data?.type === 'loaded') {
        imageEditor.contentWindow?.postMessage({
          type: "image-editor-input",
          detail: value.image
        }, '*');
      }

      if (e.data?.type === 'image-editor-output') {
        dispatch('change', { image: e.data.detail.image, mask: e.data.detail.mask });
        imageEditorValue = e.data.detail.image;
        value.mask = e.data.detail.mask;
        value.image = e.data.detail.image;
      }
  }

  onMount(() => {
    window.addEventListener('message', processMessages ,false);
  });

  onDestroy(() => {
    window.removeEventListener('message', processMessages);
  });
  </script>

<Block
	{visible}
	variant="solid"
	color={dragging ? "green" : "grey"}
	padding={false}
	{elem_id}
	style={{ rounded: style.rounded, height: style.height, width: style.width }}
>
	<StatusTracker {...loading_status} />

  <iframe bind:this="{imageEditor}" src="/static/vendor/minipaint.html" style="width: 100%; height: 600px; border: none;" title="image-editor"/>
</Block>
