// Reduced TinyLlama-like block fixture for E1 pass bring-up.
// This is intentionally small and stable. It is not a full model export.
module {
  func.func @tinyllama_block(
      %token: tensor<1x1xi32>,
      %embedding_table: tensor<32000x16xf16>,
      %q_weight: tensor<16x16xf16>,
      %k_weight: tensor<16x16xf16>,
      %v_weight: tensor<16x16xf16>,
      %o_weight: tensor<16x16xf16>,
      %mlp_up_weight: tensor<16x64xf16>,
      %mlp_down_weight: tensor<64x16xf16>) -> tensor<1x16xf16> {
    %cst_zero = stablehlo.constant dense<0.000000e+00> : tensor<1x16xf16>
    %embedded = stablehlo.gather %embedding_table, %token,
        offset_dims = [1],
        collapsed_slice_dims = [0],
        start_index_map = [0],
        index_vector_dim = 1,
        slice_sizes = array<i64: 1, 16>
        : (tensor<32000x16xf16>, tensor<1x1xi32>) -> tensor<1x16xf16>
    %q = stablehlo.dot_general %embedded, %q_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x16xf16>, tensor<16x16xf16>) -> tensor<1x16xf16>
    %k = stablehlo.dot_general %embedded, %k_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x16xf16>, tensor<16x16xf16>) -> tensor<1x16xf16>
    %v = stablehlo.dot_general %embedded, %v_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x16xf16>, tensor<16x16xf16>) -> tensor<1x16xf16>
    %attention_score = stablehlo.multiply %q, %k
        : tensor<1x16xf16>
    %attention_value = stablehlo.multiply %attention_score, %v
        : tensor<1x16xf16>
    %projected = stablehlo.dot_general %attention_value, %o_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x16xf16>, tensor<16x16xf16>) -> tensor<1x16xf16>
    %residual = stablehlo.add %projected, %cst_zero
        : tensor<1x16xf16>
    %mlp_up = stablehlo.dot_general %residual, %mlp_up_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x16xf16>, tensor<16x64xf16>) -> tensor<1x64xf16>
    %mlp_act = stablehlo.tanh %mlp_up : tensor<1x64xf16>
    %mlp_down = stablehlo.dot_general %mlp_act, %mlp_down_weight,
        contracting_dims = [1] x [0],
        precision = [DEFAULT, DEFAULT]
        : (tensor<1x64xf16>, tensor<64x16xf16>) -> tensor<1x16xf16>
    %out = stablehlo.add %mlp_down, %residual : tensor<1x16xf16>
    return %out : tensor<1x16xf16>
  }
}
